"""Historical search, per-link evidence, time boundaries, and independent pending behavior."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import config
from mta.linking import link_error
from mta.p2 import assessment_tool, bind_evidence, bounded_result, run_p2_agent, validate_outputs
from mta.agent import MeetingBudget
from mta.pipeline import make_record
from mta.trace import Trace
from jsonschema import Draft202012Validator
from mta.store import Store
from mta.triage import flag_items
from test_p2 import CUR, OLD, D4, P1, MemoryStore, Script, assessment, execute, item


@pytest.fixture
def history(tmp_path):
    store = Store(CUR, run_dir=tmp_path)
    old_da = store.meeting(OLD).acts[0]["id"]
    record = {"id": OLD + ".sys.s.1", "type": "decisions", "text": "Start with T_V_ and video", "status": "adopted",
              "reference": [{"source_type": "dialogue", "locator": old_da}]}
    path = tmp_path / (OLD + ".json")
    path.write_text(json.dumps([record]), encoding="utf-8")
    return store, record, path


def test_acronyms_and_filter_before_topk_retrieve_target_type(history):
    store, record, path = history
    distractors = [{**record, "id": OLD + f".sys.s.{n}", "type": "actions", "text": "television video television"} for n in range(2, 8)]
    path.write_text(json.dumps(distractors + [record]), encoding="utf-8")
    for query in ("TV", "T_V_", "television"):
        results = store.get_prior_items(query, "decisions", k=1)
        assert [r["id"] for r in results] == [record["id"]]


def test_history_snapshot_and_source_hash_do_not_change_mid_run(history):
    store, record, path = history
    expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    first = store.get_prior_items("TV")[0]
    path.write_text("[]", encoding="utf-8")
    second = store.get_prior_items("TV")[0]
    assert first == second and second["source_sha256"] == expected_hash
    assert store.prior_items() == [record]


@pytest.mark.parametrize("mid", ["TS3006b", "TS3006c", "TS3006d", "ES2011a"])
def test_invisible_records_and_dialogue_rejected_before_loading(history, monkeypatch, mid):
    store, _, _ = history
    monkeypatch.setattr("mta.store.parse_transcript_file", lambda *a: pytest.fail("Read forbidden transcript"))
    with pytest.raises(PermissionError):
        store.read_prior_item(mid + ".sys.s.1")
    if mid != CUR:  # current dialogue is readable; current records are not historical targets
        with pytest.raises(PermissionError):
            store.meeting(mid)


def test_record_file_cannot_smuggle_a_future_record_or_reference(history):
    store, record, path = history
    future = CUR + ".A.fake"
    record["reference"].append({"source_type": "dialogue", "locator": future})
    path.write_text(json.dumps([record, {**record, "id": CUR + ".sys.s.9"}]), encoding="utf-8")
    rows = store.read_prior_item(record["id"], window=0)
    assert len(store.prior_items()) == 1
    assert future in rows[0]["unreadable_reference_ids"]
    assert future not in rows[0]["evidence"]
    assert all(row.get("da_id") != future for row in rows)


def test_read_prior_item_delivers_actual_dialogue_and_supports_focus_and_paging(history):
    store, record, path = history
    acts = store.meeting(OLD).acts[:6]
    record["reference"] = [{"source_type": "dialogue", "locator": a["id"]} for a in acts]
    path.write_text(json.dumps([record]), encoding="utf-8")
    first = store.read_prior_item(record["id"], limit=1, window=0)
    second = store.read_prior_item(record["id"], offset=first[0]["next_offset"], limit=1, window=0)
    assert first[1]["text"] == acts[0]["text"] and first[1]["is_reference"]
    assert second[1]["da_id"] == acts[1]["id"]
    assert first[0]["text"] == record["text"] and first[0]["status"] == "adopted"
    focused = store.read_prior_item(record["id"], query=acts[5]["text"], limit=6, window=0)
    assert {r["da_id"] for r in focused[1:]} == {a["id"] for a in acts}
    delivered = bounded_result({"results": focused}, limit=1000)
    assert len(json.dumps(delivered, ensure_ascii=False)) <= 1000
    assert all(r in focused for r in delivered["results"])


def link_payload(store):
    link = {"id": store.prior["id"], "relation": "cancels", "changed_fields": ["status"],
            "reason": "The current meeting explicitly cancels the old decision",
            "prior_evidence_da_ids": [P1], "current_evidence_da_ids": [D4]}
    finding = {"result": "pass", "reason": link["reason"], "fields": [], "links": [link], "quotes": [
        {"da_id": P1, "quote": store.meeting(OLD).acts[0]["text"]},
        {"da_id": D4, "quote": store.meeting(CUR).acts[-1]["text"]}]}
    return link, finding


@pytest.mark.parametrize("mutation,reason", [
    (lambda l: l.update(id=CUR + ".sys.s.1"), "per_link"),
    (lambda l: l.update(prior_evidence_da_ids=[OLD + ".A.wrong"]), "selected_record"),
    (lambda l: l.update(current_evidence_da_ids=[P1]), "temporal_scope"),
    (lambda l: l.update(relation="continues"), "changed_fields"),
    (lambda l: l.update(relation="updates", changed_fields=[]), "identified_change"),
    (lambda l: l.update(relation="completes"), "earlier_action"),
    (lambda l: l.update(relation="resolves"), "earlier_problem"),
])
def test_link_gate_covers_each_selected_record_and_both_time_scopes(mutation, reason):
    store = MemoryStore()
    link, finding = link_payload(store)
    related = [{"id": link["id"], "relation": link["relation"]}]
    mutation(link)
    if not reason == "per_link":
        related[0]["relation"] = link["relation"]
    error = link_error(store, related, finding, {P1: {}, D4: {}}, {store.prior["id"]: store.prior})
    assert reason in error


def test_valid_link_requires_each_quote_actually_delivered():
    store = MemoryStore()
    link, finding = link_payload(store)
    related = [{"id": link["id"], "relation": link["relation"]}]
    seen = {store.prior["id"]: store.prior}
    assert link_error(store, related, finding, {P1: {}, D4: {}}, seen) is None
    assert "read_and_quote" in link_error(store, related, finding, {D4: {}}, seen)
    finding["links"] = []
    assert "per_link" in link_error(store, related, finding, {P1: {}, D4: {}}, seen)


def test_agent_can_search_again_then_stop_without_publishing_a_guessed_link():
    store, it = MemoryStore(), item("decisions")
    it["flags"] = ["POSSIBLE_PRIOR"]
    first = assessment(store, it, "retrieve")
    first["checks"]["relation"].update(result="uncertain", reason="Need historical record")
    second = copy.deepcopy(first)
    second.update(next_action="search_again")
    second["checks"]["relation"]["reason"] = "Search did not establish the previous decision"
    result, model, _, budget = execute(store, it, [("assess_item", first),
        ("get_prior_items", {"query": "LCD", "item_type": "decisions"}), ("assess_item", second),
        ("search_dialogue", {"query": "bad alternate", "scope": "prior"}),
        ("assess_item", {**second, "next_action": "stop"})])
    assert result["state"] == "pending" and result["recordable"]
    assert result["fields"]["related_items"] == [] and budget.used == 2
    assert result["clarifications"][0]["field"] == "related_items"
    assert any(e.get("decision") == "search_again" for e in result["history"])
    assert "get_prior_items" in model.calls[3]["tools"]


def test_missing_system_history_is_pending_even_after_searching_dialogue():
    store, it = MemoryStore(), item("decisions")
    it["flags"] = ["POSSIBLE_PRIOR", "PRIOR_RECORDS_UNAVAILABLE"]
    payload = assessment(store, it, "stop")
    payload["checks"]["relation"].update(result="pass")
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["state"] == "pending" and result["fields"]["related_items"] == []
    assert result["checks"]["relation"]["reason"] == "prior_records_unavailable_not_a_confirmed_no_match"
    assert result["clarifications"][0]["field"] == "related_items"


def test_missing_history_flags_decisions_as_well_as_actions(tmp_path):
    store = Store(CUR, run_dir=tmp_path)
    it = item("decisions")
    flag_items([it], store)
    assert {"POSSIBLE_PRIOR", "PRIOR_RECORDS_UNAVAILABLE"} <= set(it["flags"])


def test_agent_reads_prior_record_and_publishes_before_after_witness():
    class WithRecordRead(MemoryStore):
        def read_prior_item(self, item_id, query="", offset=0, limit=8, window=1):
            assert item_id == self.prior["id"]
            return [{"kind": "prior_item", **self.prior},
                    {"kind": "dialogue", **self.read_dialogue(OLD, P1)[0]}]
    store, it = WithRecordRead(), item("decisions")
    it.update(text="Cancel the earlier LCD choice", evidence_da_ids=[D4], flags=["POSSIBLE_PRIOR"])
    initial = assessment(store, it, "retrieve", D4)
    initial["checks"]["relation"].update(result="uncertain", reason="Historical evidence required")
    middle = copy.deepcopy(initial)
    middle["checks"]["relation"]["reason"] = "Read the returned historical record before deciding"
    final = assessment(store, it, quote_id=D4)
    link, final["checks"]["relation"] = link_payload(store)
    final["fields"]["related_items"] = [{"id": link["id"], "relation": link["relation"]}]
    final["fields"]["evidence_da_ids"].append(P1)
    focused = {"related_items": final["fields"]["related_items"],
               **{k: v for k, v in final["checks"]["relation"].items() if k != "fields"}, "next_action": "accept"}
    focused["quotes"] = [q["da_id"] for q in focused["quotes"]]
    result, model, _, budget = execute(store, it, [("assess_item", initial),
        ("get_prior_items", {"query": "LCD", "item_type": "decisions"}), ("assess_item", middle),
        ("read_prior_item", {"item_id": store.prior["id"], "window": 0}), ("assess_relation", focused)])
    assert result["state"] == "accepted" and budget.used == 2
    witness = result["relation_evidence"][0]
    assert witness["state"] == "supported" and witness["before"]["status"] == "adopted"
    assert witness["prior_quotes"][0]["da_id"] == P1 and witness["current_quotes"][0]["da_id"] == D4
    assert store.prior["status"] == "adopted"
    assert model.calls[-1]["choice"]["name"] == "assess_relation"
    assert "get_participants" not in model.calls[1]["tools"]
    assert "get_prior_items" not in model.calls[3]["tools"]


def test_quote_error_feedback_returns_actual_line_without_relaxing_validation():
    store, it = MemoryStore(), item("decisions")
    first = assessment(store, it, "retrieve")
    first["checks"]["statement"]["quotes"][0]["quote"] = "invented paraphrase"
    result, model, _, _ = execute(store, it, [("assess_item", first),
        ("stop", {"reason": "Cannot verify the statement"})])
    feedback = json.loads(model.calls[1]["messages"][-1]["content"][0]["content"])
    d = it["evidence_da_ids"][0]
    assert feedback["quote_corrections"][d] == store.meeting(CUR).acts[0]["text"]
    assert result["state"] == "pending" and not result["recordable"]
    assert result["history"][0]["submitted_checks"]["statement"]["quotes"][0]["quote"] == "invented paraphrase"


def test_explicit_history_cue_requires_comparison_even_without_lexical_match(history):
    store, _, _ = history
    it = item("decisions")
    it["text"] = "Continue the same approach as before"
    flag_items([it], store)
    assert "STATUS_CHECK" in it["flags"]


def test_history_assessment_cannot_skip_relation_and_nonconflict_fields_are_derived():
    store, it = MemoryStore(), item("decisions")
    payload = assessment(store, it)
    schema = assessment_tool(True, "decisions", require_relation=True)["input_schema"]
    assert any(list(e.path) == ["checks", "relation", "result"] for e in Draft202012Validator(schema).iter_errors(payload))
    payload["checks"]["relation"].update(result="uncertain", reason="Historical comparison required")
    for name in payload["checks"]:
        if name != "conflict":
            del payload["checks"][name]["fields"]
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


def test_verified_relation_can_remain_while_status_is_pending():
    class WithRead(MemoryStore):
        def read_prior_item(self, *args):
            return [{"kind": "prior_item", **self.prior}, {"kind": "dialogue", **self.read_dialogue(OLD, P1)[0]}]
    store, it = WithRead(), item("decisions")
    it.update(text="Cancel the LCD choice", evidence_da_ids=[D4], flags=["POSSIBLE_PRIOR"])
    payload = assessment(store, it, "retrieve", D4)
    payload["checks"]["status"].update(result="uncertain", reason="Current final state needs confirmation")
    payload["checks"]["relation"].update(result="uncertain", reason="Read the prior evidence")
    middle = copy.deepcopy(payload)
    middle["checks"]["relation"]["reason"] = "Verify the selected record's original evidence"
    link, finding = link_payload(store)
    focused = {**{k: v for k, v in finding.items() if k != "fields"},
               "related_items": [{"id": link["id"], "relation": link["relation"]}], "next_action": "request_clarification"}
    result, model, _, _ = execute(store, it, [("assess_item", payload),
        ("get_prior_items", {"query": "LCD"}), ("assess_item", middle),
        ("read_prior_item", {"item_id": store.prior["id"]}), ("assess_relation", focused)])
    assert result["state"] == "pending" and result["recordable"]
    assert result["fields"]["status"] == "unknown" and result["fields"]["related_items"]
    assert result["relation_evidence"][0]["after"]["status"] == "unknown"
    assert result["clarifications"][0]["field"] == "status"
    assert model.calls[-1]["choice"]["name"] == "assess_relation"


def test_selected_link_ids_bind_only_delivered_text_and_never_replace_a_false_quote():
    store, it = MemoryStore(), item("decisions")
    payload = assessment(store, it, quote_id=D4)
    link, finding = link_payload(store)
    payload["checks"]["relation"] = finding
    payload["checks"]["relation"]["quotes"] = []
    observed = {d: {"text": store.meeting(d.split(".")[0]).acts[store.meeting(d.split(".")[0]).by_id[d]]["text"]} for d in (P1, D4)}
    resolved, bindings = bind_evidence(payload, observed, CUR)
    assert {q["da_id"] for q in resolved["checks"]["relation"]["quotes"]} == {P1, D4}
    assert bindings["relation"] == [P1, D4] and payload["checks"]["relation"]["quotes"] == []
    partial, _ = bind_evidence(payload, {D4: observed[D4]}, CUR)
    assert [q["da_id"] for q in partial["checks"]["relation"]["quotes"]] == [D4]
    payload["checks"]["relation"]["quotes"] = [{"da_id": P1, "quote": "fabricated quote"}]
    resolved, _ = bind_evidence(payload, observed, CUR)
    assert resolved["checks"]["relation"]["quotes"][0]["quote"] == "fabricated quote"


def test_no_conflict_can_anchor_to_statement_but_uncertain_conflict_is_never_changed():
    store, it = MemoryStore(), item("decisions")
    payload = assessment(store, it)
    payload["checks"]["conflict"]["quotes"] = []
    d = it["evidence_da_ids"][0]
    observed = {d: {"text": store.meeting(CUR).acts[0]["text"]}}
    resolved, bindings = bind_evidence(payload, observed, CUR)
    assert resolved["checks"]["conflict"]["quotes"] == payload["checks"]["statement"]["quotes"]
    assert bindings["conflict_scope"] == [d]
    payload["checks"]["conflict"].update(result="uncertain", reason="Actual contradiction remains")
    resolved, bindings = bind_evidence(payload, observed, CUR)
    assert resolved["checks"]["conflict"]["result"] == "uncertain" and not bindings


def test_inapplicable_field_stop_hint_gets_one_correction_then_agent_can_still_stop():
    store, it = MemoryStore(), item("decisions")
    it["flags"] = ["POSSIBLE_PRIOR"]
    first = assessment(store, it, "request_clarification")
    first["checks"]["owner"].update(result="uncertain", reason="Missing owner")
    first["checks"]["relation"].update(result="uncertain", reason="History not checked")
    second = copy.deepcopy(first)
    second["checks"]["owner"].update(result="not_applicable")
    second["checks"]["relation"]["reason"] = "Agent chooses to leave the historical relation pending"
    result, model, _, budget = execute(store, it, [("assess_item", first), ("assess_item", second)])
    assert result["state"] == "pending" and result["recordable"] and budget.used == 0
    assert len(model.calls) == 2 and result["history"][0]["decision"] == "reassess"
    assert result["clarifications"][0]["field"] == "related_items"


@pytest.fixture(params=["link-v10", "link-v15"])
def captured_scope_case(request):
    """Replay actual provider choices, not hand-authored relation labels; no API or gold."""
    folder = config.ROOT / "results/cross_meeting" / request.param
    it = json.loads((folder / "input.json").read_text(encoding="utf-8"))["agent_input"]
    steps = [(tool["name"], tool["input"]) for line in (folder / "stages.jsonl").read_text(encoding="utf-8").splitlines()
             for tool in json.loads(line).get("response_tools", [])]
    store = Store(CUR, run_dir=folder / "prior_records")
    flag_items([it], store)
    result = run_p2_agent(Script(steps), store, it, MeetingBudget(), Trace(), CUR + "#0")
    it["fields"] = result["fields"]
    record = make_record(CUR, [it], [])
    result["record_id"] = record[0]["id"]
    p2 = {"format_version": 1, "run_id": "test-replay", "meeting_id": CUR, "enabled": True, "items": [result]}
    requests = {"format_version": 1, "run_id": "test-replay", "meeting_id": CUR, "requests": []}
    return result, record, p2, requests, store


def test_real_scope_change_survives_replay_with_bound_evidence(captured_scope_case):
    result, record, p2, requests, store = captured_scope_case
    assert result["state"] == "accepted" and result["recordable"]
    assert record[0]["related_items"] == [{"id": OLD + ".sys.s.6", "relation": "updates"}]
    assert validate_outputs(p2, requests, record, store) == []
    witness = result["relation_evidence"][0]
    assert witness["changed_fields"] == ["scope"] and "T_V_ and video" in witness["prior_quotes"][0]["quote"]
    assert "only one device" in witness["current_quotes"][0]["quote"]


@pytest.mark.parametrize("field", ["before", "current_quotes"])
def test_published_witness_must_match_actual_history_and_assessed_quotes(captured_scope_case, field):
    result, record, p2, requests, store = captured_scope_case
    witness = result["relation_evidence"][0]
    if field == "before":
        witness["before"]["text"] = "Tampered old history"
    else:
        witness["current_quotes"][0]["quote"] = "Invented current evidence"
    assert validate_outputs(p2, requests, record, store)


@pytest.mark.parametrize("objects", [False, True])
def test_lean_evidence_selection_derives_nonaction_checks_without_guessing_owners(objects):
    store, it = MemoryStore(), item("decisions")
    payload = assessment(store, it)
    for name in ("owner", "deadline"):
        del payload["checks"][name]
        del payload["fields"][name]
        del payload["fields"][name + "_basis"]
    for finding in payload["checks"].values():
        finding["quotes"] = [{"da_id": q["da_id"]} if objects else q["da_id"] for q in finding["quotes"]]
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["state"] == "accepted" and result["recordable"]
    assert result["checks"]["owner"]["result"] == result["checks"]["deadline"]["result"] == "not_applicable"
    assert all(isinstance(q, dict) and q["quote"] for finding in result["checks"].values() for q in finding["quotes"])


def test_unknown_id_selection_cannot_turn_into_quoted_evidence():
    store, it = MemoryStore(), item("decisions")
    payload = assessment(store, it, "request_clarification")
    payload["checks"]["statement"]["quotes"] = ["TS3006c.A.future"]
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["state"] == "pending" and not result["recordable"]
    assert result["checks"]["statement"]["quotes"] == []


@pytest.mark.parametrize("field,value", [("before", "broken"), ("current_quotes", "broken")])
def test_malformed_witness_types_return_validation_errors(captured_scope_case, field, value):
    result, record, p2, requests, store = captured_scope_case
    result["relation_evidence"][0][field] = value
    assert validate_outputs(p2, requests, record, store)


def test_final_live_missing_record_ablation_replays_to_pending_without_a_guessed_id():
    folder = config.ROOT / "results/cross_meeting/missing-history-v2"
    it = json.loads((folder / "input.json").read_text(encoding="utf-8"))["agent_input"]
    steps = [(tool["name"], tool["input"]) for line in (folder / "stages.jsonl").read_text(encoding="utf-8").splitlines()
             for tool in json.loads(line).get("response_tools", [])]
    store = Store(CUR, run_dir=folder / "prior_records")
    flag_items([it], store)
    result = run_p2_agent(Script(steps), store, it, MeetingBudget(), Trace(), CUR + "#0")
    assert result["state"] == "pending" and result["recordable"]
    assert result["fields"]["related_items"] == [] and result["unresolved_fields"] == ["related_items"]
    assert result["clarifications"][0]["field"] == "related_items"
