"""Passage retrieval, delivered-evidence provenance, P2 decisions, and publication. No provider calls."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import config, pipeline
from mta.agent import _execute, _ids_in
from mta.documents import ScenarioDocument, reference
from mta.p2 import bounded_result, document_context_tool, evaluate, run_p2_agent, safe_fields, validate_outputs
from mta.store import BM25, Store, history_tokens
from mta.validate import check
from mta.llm import LLM
from mta.control import Limits, RunControl, RunStopped
from mta.agent import MeetingBudget
from mta.trace import Trace
from test_p2 import CUR, D1, MemoryStore, Script, assessment, execute, item
from test_observation import FixtureClient


@pytest.fixture
def docstore(tmp_path):
    path = tmp_path / "scenario.txt"
    path.write_text("=== PDF PAGE 1 ===\nTime: 3:00:00\nRole: Industrial Designer\n" +
                    ("Introductory background for this product.\n" * 50) +
                    "An LCD display requires an advanced chip.\n" +
                    "=== PDF PAGE 2 ===\nUnrelated description of packaging.\n", encoding="utf-8")
    class WithDocuments(MemoryStore):
        search_project_doc = Store.search_project_doc
        read_project_doc = Store.read_project_doc
        def prior_items(self):
            return []
    store = WithDocuments()
    store.allow_prior = False
    store.meetings[CUR].acts[0]["text"] = "We agreed to include an LCD with the advanced chip."
    store.documents = ScenarioDocument(path, BM25, history_tokens)
    store.path = path
    return store


def decision():
    it = item("decisions")
    it["text"] = "Use an LCD with the advanced chip"
    return it


def selection(row, result="context"):
    return {"passage_id": row["passage_id"], "result": result,
            "reason": "This passage explains the display's component requirement"}


def test_query_matches_the_end_of_a_page_instead_of_the_first_500_characters(docstore):
    rows = docstore.search_project_doc("LCD advanced chip")
    assert rows and "LCD display requires an advanced chip" in rows[0]["text"]
    assert rows[0]["start"] > 500 and rows[0]["page"] == 1
    assert all(docstore.documents.valid_row(r) for r in rows)


def test_paged_read_covers_exactly_the_whole_page_and_snapshot_is_immutable(docstore):
    original_hash = docstore.documents.sha256
    docstore.path.write_text("replacement", encoding="utf-8")
    offset, rows = 0, []
    while True:
        row = docstore.read_project_doc(1, offset, 350)[0]
        rows.append(row)
        if row["next_offset"] is None:
            break
        offset = row["next_offset"]
    assert "".join(r["text"] for r in rows) == docstore.documents.pages[1]
    assert len({r["passage_id"] for r in rows}) == len(rows)
    assert all(r["source_sha256"] == original_hash for r in rows)
    assert docstore.documents.valid_row(rows[-1])


@pytest.mark.parametrize("args,error", [((999, 0, 100), KeyError), ((1, -1, 100), ValueError),
    ((1, 100000, 100), ValueError), ((1, 0, 0), ValueError), ((1, 0, 2001), ValueError)])
def test_read_rejects_unknown_pages_and_invalid_ranges(docstore, args, error):
    with pytest.raises(error):
        docstore.read_project_doc(*args)


def test_omitted_tool_rows_cannot_become_document_evidence(docstore):
    row = docstore.search_project_doc("LCD advanced chip")[0]
    assert not bounded_result({"results": [row]}, limit=50)["results"]
    it = decision(); payload = assessment(docstore, it, "request_clarification")
    payload["checks"]["statement"]["documents"] = [selection(row)]
    outcome, _, _, _ = execute(docstore, it, [("assess_item", payload)])
    assert not outcome["recordable"]
    assert all(r["source_type"] != "project_document" for r in outcome["fields"]["reference"])
    assert outcome["checks"]["statement"]["reason"] == "document_passage_not_delivered"


@pytest.fixture
def captured_doc_case(docstore):
    it = decision()
    initial = assessment(docstore, it, "retrieve")
    initial["checks"]["statement"].update(result="uncertain", reason="Need to verify the scenario component context")
    middle = copy.deepcopy(initial)
    middle["checks"]["statement"]["reason"] = "Read the relevant page context before selecting support"
    hit = docstore.search_project_doc("LCD advanced chip")[0]
    read = docstore.read_project_doc(hit["page"], hit["start"], 1800)[0]
    final = assessment(docstore, it)
    final["checks"]["statement"]["documents"] = [selection(read)]
    result, model, trace, budget = execute(docstore, it, [("assess_item", initial),
        ("search_project_doc", {"query": "LCD advanced chip"}), ("assess_item", middle),
        ("read_project_doc", {"page": hit["page"], "offset": hit["start"]}), ("assess_item", final)])
    it["fields"] = result["fields"]
    record = pipeline.make_record(CUR, [it], [])
    result["record_id"] = record[0]["id"]
    p2 = {"format_version": 1, "run_id": "doc-test", "meeting_id": CUR, "enabled": True, "items": [result]}
    requests = {"format_version": 1, "run_id": "doc-test", "meeting_id": CUR, "requests": []}
    return docstore, result, record, p2, requests, budget


def test_selected_document_survives_p2_and_record_assembly(captured_doc_case):
    store, result, record, p2, requests, budget = captured_doc_case
    assert result["state"] == "accepted" and budget.used == 2
    docs = [r for r in record[0]["reference"] if r["source_type"] == "project_document"]
    assert docs == [reference(result["document_evidence"][0]["evidence"])]
    assert "chars" in docs[0]["locator"]
    assert check(record, store) == [] and validate_outputs(p2, requests, record, store) == []


def test_unselected_search_hits_are_not_published(docstore):
    it = decision(); initial = assessment(docstore, it, "retrieve")
    initial["checks"]["statement"].update(result="uncertain", reason="Check context")
    final = assessment(docstore, it)
    result, _, _, _ = execute(docstore, it, [("assess_item", initial),
        ("search_project_doc", {"query": "LCD advanced chip"}), ("assess_item", final)])
    assert result["observed_documents"] and result["state"] == "accepted"
    assert not result["document_evidence"]
    assert all(r["source_type"] != "project_document" for r in result["fields"]["reference"])


@pytest.mark.parametrize("document_result,expected", [("contradicts", "uncertain"), ("uncertain", "uncertain"), ("irrelevant", "pass")])
def test_document_findings_drive_pending_or_exclusion(docstore, document_result, expected):
    it = decision(); hit = docstore.search_project_doc("LCD advanced chip")[0]
    payload = assessment(docstore, it)
    payload["checks"]["statement"]["documents"] = [selection(hit, document_result)]
    observed = {D1: docstore.read_dialogue(CUR, D1)[0]}
    checks, fields = evaluate(docstore, it, payload, observed, {}, set(), documents_seen={hit["passage_id"]: hit})
    fields, recordable = safe_fields(docstore, it, payload["fields"], checks, fields, {hit["passage_id"]: hit})
    assert checks["statement"]["result"] == expected
    assert all(r["source_type"] != "project_document" for r in fields["reference"])


@pytest.mark.parametrize("change", ["source", "range", "text", "delivery", "citation", "witness"])
def test_document_provenance_and_output_cannot_be_tampered(captured_doc_case, change):
    store, result, record, p2, requests, _ = captured_doc_case
    row = result["observed_documents"][-1]
    if change == "source": row["source_sha256"] = "0" * 64
    elif change == "range": row["end"] += 1
    elif change == "text": row["text"] = "Invented source text"
    elif change == "delivery":
        for e in result["history"]:
            if e.get("action") in ("search_project_doc", "read_project_doc"):
                e["tool_result"]["results"] = []
    elif change == "citation":
        result["fields"]["reference"] += [{"source_type": "project_document", "source": "amiScenario.pdf", "locator": "PDF page 2 (one-based)"}]
    else: result["document_evidence"][0]["reason"] = "Tampered model assessment"
    assert validate_outputs(p2, requests, record, store)


@pytest.mark.parametrize("field", ["owner", "deadline"])
def test_scenario_cannot_replace_current_assignment_or_deadline_evidence(docstore, field):
    it = item("actions"); payload = assessment(docstore, it)
    hit = docstore.search_project_doc("LCD advanced chip")[0]
    payload["checks"][field].update(quotes=[], documents=[selection(hit, "supports")])
    checks, _ = evaluate(docstore, it, payload, {D1: docstore.read_dialogue(CUR, D1)[0]}, {}, set(),
                         documents_seen={hit["passage_id"]: hit})
    assert checks[field]["result"] == "uncertain" and checks[field]["reason"] == "current_meeting_support_required"


def test_different_portions_of_same_page_are_new_observations(docstore):
    first = _execute(docstore, "read_project_doc", {"page": 1, "limit": 300})
    second = _execute(docstore, "read_project_doc", {"page": 1, "offset": 300, "limit": 300})
    assert _ids_in("read_project_doc", first).isdisjoint(_ids_in("read_project_doc", second))


def test_context_request_cannot_be_silently_completed_without_document_review(docstore):
    it = decision(); it["verify_project_context"] = True
    payload = assessment(docstore, it, "stop")
    result, _, _, _ = execute(docstore, it, [("assess_item", payload)])
    assert not result["recordable"] and result["state"] == "pending"
    assert result["checks"]["statement"]["reason"] == "requested_document_context_not_checked"
    assert "scenario passage" in result["clarifications"][0]["question"]


def test_identical_root_duplicates_are_logged_without_changing_decisions(docstore):
    it = decision(); payload = assessment(docstore, it)
    payload["related_items"] = copy.deepcopy(payload["fields"]["related_items"])
    result, _, _, _ = execute(docstore, it, [("assess_item", payload)])
    assert result["state"] == "accepted"
    assert result["history"][0]["raw_tool_input"]["related_items"] == []
    assert result["history"][0]["protocol_normalizations"] == ["removed_identical_root_duplicate:related_items"]


def test_conflicting_root_duplicates_are_rejected(docstore):
    it = decision(); payload = assessment(docstore, it)
    payload["related_items"] = [{"id": "invented", "relation": "updates"}]
    result, _, _, _ = execute(docstore, it, [("assess_item", payload), ("assess_item", payload)])
    assert result["state"] == "pending" and not result["recordable"]
    assert result["termination_reason"] == "invalid_action_limit"


def test_pipeline_publishes_and_resumes_document_citations_without_changing_record_schema(tmp_path, monkeypatch):
    scenario = tmp_path / "scenario.txt"
    scenario.write_text("=== PDF PAGE 1 ===\nThis remote-control project includes prototype and menu design.\n", encoding="utf-8")
    monkeypatch.setattr(config, "SCENARIO", scenario)
    class DocumentClient(FixtureClient):
        def __init__(self):
            self.checks = 0
            super().__init__()
        def create(self, **kwargs):
            choice = kwargs["tool_choice"]
            if choice.get("type") == "any":
                return NS(content=[NS(type="tool_use", id="doc-search", name="search_project_doc", input={"query": "prototype menu"})],
                          usage=NS(input_tokens=10, output_tokens=5))
            response = super().create(**kwargs)
            if choice.get("name") == "assess_item":
                payload = response.content[0].input
                if self.checks == 0:
                    payload["checks"]["statement"].update(result="uncertain", reason="Verify project instructions")
                    payload["next_action"] = "retrieve"
                else:
                    prior = next(m for m in reversed(kwargs["messages"]) if m["role"] == "user")
                    rows = json.loads(prior["content"][0]["content"])["results"]
                    payload["checks"]["statement"]["documents"] = [selection(rows[0])]
                self.checks += 1
            return response
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", LLM(DocumentClient(), model="fixture"), tmp_path / "out", tmp_path / "cache")
    assert result["status"] == "COMPLETED", result
    record = json.loads(Path(result["output"]).read_text())
    p2 = json.loads(Path(result["p2_artifact"]).read_text())
    assert any(r["source_type"] == "project_document" for r in record[0]["reference"])
    assert p2["items"][0]["document_evidence"][0]["retained"]
    assert "amiScenario.pdf" in Path(result["output"]).with_name("TS3006a_report.md").read_text()
    assert pipeline.saved_run_complete(Path(result["output"]))
    metadata_path = Path(result["output"]).with_suffix(".run.json")
    original_source = scenario.read_bytes()
    scenario.write_bytes(original_source + b"\nChanged scenario context")
    assert not pipeline.saved_run_complete(Path(result["output"]))
    scenario.write_bytes(original_source)
    assert pipeline.saved_run_complete(Path(result["output"]))
    with monkeypatch.context() as changed:
        changed.setattr(config, "SCENARIO_PASSAGE_CHARS", 800)
        assert not pipeline.saved_run_complete(Path(result["output"]))
    metadata = json.loads(metadata_path.read_text()); metadata["config"].pop("DOCUMENT_POLICY_VERSION")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert not pipeline.saved_run_complete(Path(result["output"]))


def test_missing_pass_evidence_gets_one_bounded_reassessment_without_automatic_acceptance(docstore):
    it = decision(); first = assessment(docstore, it, "retrieve")
    del first["checks"]["status"]["quotes"]
    result, model, _, budget = execute(docstore, it, [("assess_item", first), ("assess_item", assessment(docstore, it))])
    assert result["state"] == "accepted" and budget.used == 0 and len(model.calls) == 2
    assert result["history"][0]["decision"] == "reassess"
    second_feedback = json.loads(model.calls[1]["messages"][-1]["content"][0]["content"])
    assert "evidence_selection_correction" in second_feedback
    assert "applicability_correction" not in second_feedback


def test_agent_can_stop_instead_of_retrying_missing_evidence(docstore):
    it = decision(); payload = assessment(docstore, it, "stop")
    del payload["checks"]["status"]["quotes"]
    payload["documents"] = []
    result, model, _, _ = execute(docstore, it, [("assess_item", payload)])
    assert result["state"] == "pending" and result["fields"]["status"] == "unknown" and len(model.calls) == 1
    assert "removed_empty_root_documents" in result["history"][0]["protocol_normalizations"]


def test_explicit_context_request_can_conclude_a_reviewed_passage_is_irrelevant(docstore):
    it = decision(); it["verify_project_context"] = True
    first = assessment(docstore, it, "retrieve")
    last = assessment(docstore, it)
    hit = docstore.search_project_doc("packaging")[0]
    last["checks"]["statement"]["documents"] = [selection(hit, "irrelevant")]
    result, model, _, _ = execute(docstore, it, [("assess_item", first),
        ("search_project_doc", {"query": "packaging"}), ("assess_item", last)])
    assert result["state"] == "accepted" and not result["document_evidence"][0]["retained"]
    assert all(r["source_type"] != "project_document" for r in result["fields"]["reference"])
    assert model.calls[1]["tools"] == ["search_project_doc", "read_project_doc", "request_clarification", "stop"]


def test_focused_document_assessment_can_read_more_then_publish_selected_context(docstore):
    it = decision(); it["verify_project_context"] = True
    first = assessment(docstore, it, "retrieve")
    hit = docstore.search_project_doc("LCD advanced chip")[0]
    more = {"result": "uncertain", "reason": "Need surrounding page context", "quotes": [D1],
            "documents": [], "next_action": "retrieve"}
    read = docstore.read_project_doc(hit["page"], hit["start"], 1800)[0]
    final = {"result": "pass", "reason": "Current decision is supported and the scenario explains the chip requirement",
             "quotes": [D1], "documents": [selection(read)], "next_action": "accept"}
    result, model, _, budget = execute(docstore, it, [("assess_item", first),
        ("search_project_doc", {"query": "LCD advanced chip"}), ("assess_document_context", more),
        ("read_project_doc", {"page": hit["page"], "offset": hit["start"]}), ("assess_document_context", final)])
    assert result["state"] == "accepted" and budget.used == 2
    assert model.calls[2]["choice"]["name"] == model.calls[4]["choice"]["name"] == "assess_document_context"
    assert result["document_evidence"][0]["evidence"]["origin"] == "read"


def test_document_tool_keeps_gateway_compatible_flat_root_schema():
    schema = document_context_tool("decisions")["input_schema"]
    assert schema["type"] == "object"
    assert not {"allOf", "anyOf", "oneOf"}.intersection(schema)


def test_document_check_and_lookups_share_model_budget_and_keep_received_passages(docstore):
    it = decision(); it["verify_project_context"] = True
    first = assessment(docstore, it, "retrieve")
    class Transport:
        def __init__(self):
            self.calls = 0
            self.messages = NS(create=self.create)
        def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name, payload = "assess_item", first
            else:
                name, payload = "search_project_doc", {"query": "LCD advanced chip"}
            return NS(content=[NS(type="tool_use", id="budget-doc" + str(self.calls), name=name, input=payload)],
                      usage=NS(input_tokens=10, output_tokens=5))
    client, control, checkpoints = Transport(), RunControl(Limits(2, 1000000, 1, 5, 2, 0, 0)), []
    try:
        with pytest.raises(RunStopped, match="call_budget_exhausted"):
            run_p2_agent(LLM(client).scoped(control), docstore, it, MeetingBudget(), Trace(), CUR + "#0", checkpoints.append)
        assert client.calls == control.snapshot()["attempted_calls"] == 2
        assert checkpoints[-1]["observed_documents"]
        assert checkpoints[-1]["history"][-1]["action"] == "search_project_doc"
        assert not checkpoints[-1]["recordable"]
    finally:
        control.seal()


def test_tool_limit_leaves_document_context_pending_with_its_checkpoint(docstore, monkeypatch):
    monkeypatch.setattr(config, "MAX_CALLS_PER_ITEM", 1)
    it = decision(); it["verify_project_context"] = True
    first = assessment(docstore, it, "retrieve")
    more = {"result": "uncertain", "reason": "Need surrounding page context", "quotes": [D1],
            "documents": [], "next_action": "retrieve"}
    result, _, _, budget = execute(docstore, it, [("assess_item", first),
        ("search_project_doc", {"query": "LCD advanced chip"}), ("assess_document_context", more)])
    assert result["state"] == "pending" and result["termination_reason"] == "lookup_limit" and budget.used == 1
    assert result["observed_documents"] and not result["recordable"]


def test_real_development_document_page_can_be_reconstructed_from_bounded_reads():
    store = Store("TS3006d", allow_prior=False)
    hit = next(r for r in store.search_project_doc("LCD display advanced chip") if r["page"] == 66)
    rows, offset = [], 0
    while True:
        row = _execute(store, "read_project_doc", {"page": hit["page"], "offset": offset})["results"][0]
        rows.append(row)
        if row["next_offset"] is None:
            break
        offset = row["next_offset"]
    assert "".join(r["text"] for r in rows) == store.documents.pages[66]
    assert all(store.documents.valid_row(r) for r in rows)
    assert len(rows) == 2


@pytest.mark.parametrize("capture", ["scenario-v5", "scenario-v7", "evaluation-v2"])
def test_real_development_responses_replay_with_citations_or_pending_preserved(capture):
    folder = config.ROOT / "results/scenario_context" / capture
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    it = json.loads((folder / "input.json").read_text(encoding="utf-8"))["selected_item"]
    steps = [(tool["name"], tool["input"]) for line in (folder / "stages.jsonl").read_text(encoding="utf-8").splitlines()
             for tool in json.loads(line).get("response_tools", [])]
    store = Store("TS3006d", allow_prior=False)
    from mta.triage import flag_items
    flag_items([it], store)
    result = run_p2_agent(Script(steps), store, it, MeetingBudget(), Trace(), "TS3006d#0")
    it["fields"] = result["fields"]
    record = pipeline.make_record(store.current, [it] if result["recordable"] else [], [])
    result["record_id"] = record[0]["id"] if record else None
    for request in result["clarifications"]:
        request["record_id"] = result["record_id"]
    p2 = {"format_version": 1, "run_id": "replay", "meeting_id": store.current, "enabled": True, "items": [result]}
    questions = {"format_version": 1, "run_id": "replay", "meeting_id": store.current, "requests": result["clarifications"]}
    assert result["state"] == run["outcome"]["state"]
    assert record == json.loads((folder / "TS3006d.json").read_text(encoding="utf-8"))
    assert check(record, store) == [] and validate_outputs(p2, questions, record, store) == []
    if capture == "scenario-v5":
        assert result["state"] == "accepted" and len(result["document_evidence"]) == 2
        assert all(w["retained"] for w in result["document_evidence"])
    else:
        assert result["state"] == "pending" and not record
        assert not any(w["retained"] for w in result["document_evidence"])
