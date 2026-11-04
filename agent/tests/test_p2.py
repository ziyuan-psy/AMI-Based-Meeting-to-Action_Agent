"""P2 transitions with real tool execution over a tiny visible store. No provider calls."""
import copy
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import config, observation, pipeline  # noqa: E402
from mta.agent import MeetingBudget, run_agent  # noqa: E402
from mta.control import Limits, RunControl, RunStopped  # noqa: E402
from mta.llm import LLM  # noqa: E402
from mta.p2 import CHECKS, bounded_result, evaluate, provisional, run_p2_agent, safe_fields, validate_outputs  # noqa: E402
from mta.store import Meeting, Store  # noqa: E402
from mta.trace import Trace  # noqa: E402
from mta.triage import flag_items  # noqa: E402
from test_observation import FixtureClient, fixture_llm  # noqa: E402

CUR, OLD = "TS3006b", "TS3006a"
D1, D2, D3, D4, P1 = CUR + ".A.1", CUR + ".B.2", CUR + ".A.3", CUR + ".A.4", OLD + ".A.1"


class MemoryStore:
    current, order, allow_prior = CUR, 2, True
    def __init__(self):
        def act(identifier, text, role="PM", participant="P1"):
            return {"id": identifier, "text": text, "attributes": {"role": role, "participant": participant}}
        self.meetings = {CUR: Meeting(CUR, "TS3006", 2, [
            act(D1, "The UI will develop the menu by next meeting."),
            act(D2, "I will develop the menu by next meeting.", "UI", "P2"),
            act(D3, "Actually, the ID owns the menu task.", "ID", "P3"),
            act(D4, "We cancel the previous decision to include an LCD.")]),
            OLD: Meeting(OLD, "TS3006", 1, [act(P1, "We decided to include an LCD.")])}
        self.prior = {"id": OLD + ".sys.s.1", "type": "decisions", "text": "Include an LCD", "status": "adopted", "evidence": [P1]}
        self.searches = []
    def visible_ids(self, scope="both"):
        return ([CUR] if scope in ("current", "both") else []) + ([OLD] if self.allow_prior and scope in ("prior", "both") else [])
    def meeting(self, mid, path=None):
        if mid not in self.visible_ids():
            raise PermissionError(mid)
        return self.meetings[mid]
    def da_exists(self, mid, d):
        return mid in self.visible_ids() and d in self.meetings[mid].by_id
    def roster(self, mid):
        self.meeting(mid)
        return [{"participant_id": "P1", "role": "PM"}, {"participant_id": "P2", "role": "UI"}, {"participant_id": "P3", "role": "ID"}]
    def participant_ids(self):
        return {"P1", "P2", "P3"}
    def metadata_locator(self, *args):
        return None
    def prior_item_exists(self, identifier):
        return self.allow_prior and identifier == self.prior["id"]
    def get_prior_items(self, query, item_type=None, k=3):
        return [self.prior] if self.allow_prior else []
    def read_dialogue(self, mid, da_id, window=8):
        m = self.meeting(mid)
        a = m.acts[m.by_id[da_id]]
        return [{"da_id": a["id"], "text": a["text"], **a["attributes"]}]
    def search_dialogue(self, query, scope, k=5):
        self.searches.append(query)
        return [] if "bad" in query.lower() else self.read_dialogue(CUR, D2)
    def search_project_doc(self, *args):
        return []


def item(typ="actions"):
    return {"type": typ, "text": "UI will develop the menu", "evidence_da_ids": [D1], "flags": [],
            "owner_ids": ["P2"], "owner_basis": "explicit", "deadline": "next meeting", "deadline_basis": "explicit",
            "status": "open" if typ == "actions" else "adopted"}


def assessment(store, it, next_action="accept", quote_id=D1):
    a = store.meeting(quote_id.split(".")[0]); line = a.acts[a.by_id[quote_id]]
    quote = [{"da_id": quote_id, "quote": line["text"]}]
    checks = {k: {"result": "pass", "reason": "Supported by the supplied dialogue", "quotes": quote, "fields": []} for k in CHECKS}
    checks["relation"].update(result="not_applicable", quotes=[])
    if it["type"] != "actions":
        for k in ("owner", "deadline"):
            checks[k].update(result="not_applicable", quotes=[])
    return copy.deepcopy({"fields": provisional(it), "checks": checks, "next_action": next_action, "reason": "Check available evidence"})


class Script:
    p2_legacy_input = True  # Explicit adapter for saved v1 responses; live clients use the flat protocol.
    def __init__(self, steps):
        self.steps, self.calls = list(steps), []
    def create(self, *args, **kwargs):
        self.calls.append({"choice": args[3], "tools": [t["name"] for t in args[2]], "stage": kwargs["stage"],
                           "messages": copy.deepcopy(args[1])})
        name, payload = self.steps.pop(0)
        return NS(content=[NS(type="tool_use", id="step" + str(len(self.calls)), name=name, input=copy.deepcopy(payload))])


def execute(store, it, steps, **kwargs):
    model, trace, budget = Script(steps), Trace(), MeetingBudget()
    result = run_p2_agent(model, store, it, budget, trace, CUR + "#0", **kwargs)
    return result, model, trace, budget


def test_supported_item_is_accepted_without_a_lookup():
    store, it = MemoryStore(), item()
    result, model, trace, budget = execute(store, it, [("assess_item", assessment(store, it))])
    assert result["state"] == "accepted" and result["recordable"]
    assert result["fields"]["owner"] == ["P2"] and result["clarifications"] == [] and budget.used == 0
    assert trace.events[-1]["action"] == "accept" and model.calls[0]["choice"]["name"] == "assess_item"


def test_check_reasons_are_required_but_redundant_overall_reason_is_optional():
    store, it = MemoryStore(), item()
    payload = assessment(store, it); del payload["reason"]
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["state"] == "accepted"


def test_controller_can_choose_a_safe_action_without_an_optional_model_hint():
    store, it = MemoryStore(), item()
    payload = assessment(store, it); del payload["next_action"]
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["state"] == "accepted"


def test_format_error_counter_resets_after_a_valid_step():
    store, it = MemoryStore(), item()
    first = assessment(store, it, "retrieve"); first["checks"]["owner"].update(result="uncertain", quotes=[])
    result, _, _, _ = execute(store, it, [("finish", {}), ("assess_item", first),
        ("read_dialogue", {"meeting_id": CUR, "da_id": D2}), ("finish", {}), ("assess_item", assessment(store, it))])
    assert result["state"] == "accepted"


def test_required_conflict_check_cannot_be_skipped_and_error_names_the_field():
    store, it = MemoryStore(), item()
    wrong = assessment(store, it)
    wrong["checks"]["conflict"]["result"] = "not_applicable"
    result, model, _, _ = execute(store, it, [("assess_item", wrong), ("assess_item", assessment(store, it))])
    assert result["state"] == "accepted"
    assert "checks/conflict/result" in model.calls[1]["messages"][-1]["content"][0]["content"]


def test_new_dialogue_ids_without_improved_checks_still_count_as_no_progress():
    store, it = MemoryStore(), item()
    payload = assessment(store, it, "retrieve")
    payload["checks"]["statement"].update(result="uncertain", reason="Still missing support", quotes=[])
    result, _, _, _ = execute(store, it, [("assess_item", payload),
        ("read_dialogue", {"meeting_id": CUR, "da_id": D2}), ("assess_item", payload),
        ("read_dialogue", {"meeting_id": CUR, "da_id": D3}), ("assess_item", payload)])
    assert result["state"] == "pending" and result["termination_reason"] == "no_progress"
    assert result["lookups"] == 2


def test_failed_check_overrides_accept_and_drives_read_then_recheck():
    store, it = MemoryStore(), item()
    first = assessment(store, it)
    first["checks"]["owner"].update(result="uncertain", quotes=[], reason="Need assignment context")
    last = assessment(store, it, quote_id=D2)
    last["fields"]["evidence_da_ids"].append(D2)
    snapshots = []
    result, _, trace, budget = execute(store, it, [("assess_item", first),
        ("read_dialogue", {"meeting_id": CUR, "da_id": D2}), ("assess_item", last)], progress=snapshots.append)
    assert result["state"] == "accepted" and budget.used == 1
    assert trace.events[0]["decision"] == "retrieve" and trace.events[0]["requested_action"] == "accept"
    assert trace.events[1]["tool_result"]["results"][0]["da_id"] == D2
    assert snapshots[0]["state"] == "running" and snapshots[-1]["state"] == "accepted"


def test_unhelpful_query_drives_a_different_search_and_then_accept():
    store, it = MemoryStore(), item()
    first = assessment(store, it, "retrieve")
    first["checks"]["statement"].update(result="uncertain", quotes=[])
    again = copy.deepcopy(first); again["next_action"] = "search_again"
    result, model, trace, _ = execute(store, it, [("assess_item", first),
        ("search_dialogue", {"query": "bad query", "scope": "current"}), ("assess_item", again),
        ("search_dialogue", {"query": "menu owner", "scope": "current"}), ("assess_item", assessment(store, it))])
    assert result["state"] == "accepted" and result["lookups"] == 2
    assert store.searches == ["bad query", "menu owner"]
    assert trace.events[2]["decision"] == "search_again"
    assert "read_dialogue" not in model.calls[3]["tools"]


def test_duplicate_keywords_stop_without_reexecuting_the_tool():
    store, it = MemoryStore(), item()
    first = assessment(store, it, "retrieve"); first["checks"]["statement"].update(result="uncertain", quotes=[])
    again = copy.deepcopy(first); again["next_action"] = "search_again"
    result, _, _, budget = execute(store, it, [("assess_item", first),
        ("search_dialogue", {"query": "bad query", "scope": "current"}), ("assess_item", again),
        ("search_dialogue", {"query": "QUERY bad BAD", "scope": "current"})])
    assert result["state"] == "pending" and result["termination_reason"] == "no_progress"
    assert not result["recordable"] and len(store.searches) == 1 and budget.used == 2


def test_conflicting_owner_is_cleared_and_gets_an_evidence_linked_question():
    store, it = MemoryStore(), item(); it["evidence_da_ids"].append(D3)
    payload = assessment(store, it, "request_clarification")
    payload["checks"]["conflict"].update(result="fail", reason="Two assigned roles disagree", fields=["owner"],
        quotes=[{"da_id": d, "quote": store.meeting(CUR).acts[store.meeting(CUR).by_id[d]]["text"]} for d in (D1, D3)])
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["recordable"] and result["state"] == "pending"
    assert result["fields"]["owner"] is None and result["fields"]["owner_basis"] == "ambiguous"
    question = result["clarifications"][0]
    assert question["field"] == "owner" and question["evidence_da_ids"] == [D1, D3]
    assert question["request_id"] and question["question"] and question["reason"]


def test_missing_deadline_cannot_be_labeled_accepted():
    store, it = MemoryStore(), item(); it.update(deadline=None, deadline_basis="missing")
    payload = assessment(store, it, "request_clarification")
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["state"] == "pending" and result["recordable"]
    assert result["fields"]["deadline"] is None and result["fields"]["deadline_basis"] == "missing"
    assert result["clarifications"][0]["field"] == "deadline"


@pytest.mark.parametrize("quote_id,quote", [(D2, "I will develop the menu by next meeting."), (D1, "invented quote"), (D1, " ")])
def test_unread_fabricated_or_blank_quote_cannot_pass(quote_id, quote):
    store, it = MemoryStore(), item("decisions")
    payload = assessment(store, it, "request_clarification")
    payload["checks"]["statement"]["quotes"] = [{"da_id": quote_id, "quote": quote}]
    result, _, _, _ = execute(store, it, [("assess_item", payload)])
    assert result["state"] == "pending" and not result["recordable"]


def test_prior_only_quote_cannot_prove_a_current_commitment():
    store, it = MemoryStore(), item("decisions")
    payload = assessment(store, it, "request_clarification", P1)
    line = store.meeting(OLD).acts[0]
    checks, fields = evaluate(store, it, payload, {P1: line}, {}, set())
    assert checks["statement"]["result"] == "uncertain"
    assert safe_fields(store, it, payload["fields"], checks, fields)[1] is False


def test_relation_requires_retrieving_record_and_reading_its_actual_evidence():
    store, it = MemoryStore(), item("decisions")
    it.update(text="Cancel the LCD choice", evidence_da_ids=[D4], flags=["POSSIBLE_PRIOR"])
    initial = assessment(store, it, "retrieve", D4)
    initial["checks"]["relation"].update(result="uncertain", reason="Find the earlier choice")
    need_read = copy.deepcopy(initial)
    need_read["fields"]["related_items"] = [{"id": store.prior["id"], "relation": "cancels"}]
    final = copy.deepcopy(need_read)
    final["fields"]["evidence_da_ids"].append(P1)
    final["checks"]["relation"].update(result="pass", quotes=[
        {"da_id": D4, "quote": store.meeting(CUR).acts[-1]["text"]},
        {"da_id": P1, "quote": store.meeting(OLD).acts[0]["text"]}])
    final["checks"]["relation"]["links"] = [{"id": store.prior["id"], "relation": "cancels",
        "changed_fields": ["status"], "reason": "Current meeting explicitly cancels the earlier LCD choice",
        "prior_evidence_da_ids": [P1], "current_evidence_da_ids": [D4]}]
    result, _, _, _ = execute(store, it, [("assess_item", initial),
        ("get_prior_items", {"query": "LCD", "item_type": "decisions"}), ("assess_item", need_read),
        ("read_dialogue", {"meeting_id": OLD, "da_id": P1}), ("assess_item", final)])
    assert result["state"] == "accepted" and result["fields"]["prior_context_needed"]
    assert result["fields"]["related_items"][0]["relation"] == "cancels"
    assert store.prior["status"] == "adopted"  # earlier history was read, never rewritten


def test_lookup_limit_and_invalid_actions_end_pending(monkeypatch):
    store, it = MemoryStore(), item()
    monkeypatch.setattr(config, "MAX_CALLS_PER_ITEM", 0)
    payload = assessment(store, it); payload["checks"]["owner"].update(result="uncertain", quotes=[])
    result, model, _, _ = execute(store, it, [("assess_item", payload)])
    assert len(model.calls) == 1 and result["termination_reason"] == "lookup_limit"
    result, _, _, _ = execute(store, it, [("finish", {}), ("finish", {})])
    assert result["state"] == "pending" and not result["recordable"]
    assert result["termination_reason"] == "invalid_action_limit"


def test_s1_and_first_meeting_never_offer_prior_tools():
    store, it = MemoryStore(), item(); store.allow_prior = False
    payload = assessment(store, it, "retrieve"); payload["checks"]["owner"].update(result="uncertain", quotes=[])
    result, model, _, _ = execute(store, it, [("assess_item", payload), ("request_clarification", {"reason": "Cannot determine owner"})])
    assert "get_prior_items" not in model.calls[1]["tools"] and result["state"] == "pending"
    with pytest.raises(PermissionError):
        Store("TS3006a").meeting("TS3006b")


def test_bounded_tool_result_stays_valid_json_without_registering_omitted_rows():
    rows = [{"da_id": "one", "text": "a" * 2000}, {"da_id": "two", "text": "b" * 2000}]
    result = bounded_result({"results": rows}, 2500)
    assert len(json.dumps(result, ensure_ascii=False)) <= 2500
    assert result["results"] == rows[:1] and result["omitted_results"] == 1


def test_pipeline_publishes_linked_questions_and_verifies_both_sidecar_hashes(tmp_path):
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "COMPLETED", result
    record = json.loads(Path(result["output"]).read_text(encoding="utf8"))
    p2 = json.loads(Path(result["p2_artifact"]).read_text(encoding="utf8"))
    requests = json.loads(Path(result["clarifications_artifact"]).read_text(encoding="utf8"))["requests"]
    assert p2["items"][0]["state"] == "pending" and requests[0]["field"] == "deadline"
    assert requests[0]["record_id"] == record[0]["id"]
    assert pipeline.saved_run_complete(Path(result["output"]))
    Path(result["clarifications_artifact"]).write_text("{}", encoding="utf8")
    assert not pipeline.saved_run_complete(Path(result["output"]))


def test_sidecar_validation_rejects_pending_fields_without_questions_and_wrong_record_links(tmp_path):
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    record = json.loads(Path(result["output"]).read_text(encoding="utf8"))
    p2 = json.loads(Path(result["p2_artifact"]).read_text(encoding="utf8"))
    requests = json.loads(Path(result["clarifications_artifact"]).read_text(encoding="utf8"))
    store = Store("TS3006a", allow_prior=False)
    assert validate_outputs(p2, requests, record, store) == []
    p2["items"][0]["record_id"] = "TS3006a.sys.s.999"
    assert validate_outputs(p2, requests, record, store)
    p2["items"][0]["record_id"] = record[0]["id"]
    p2["items"][0]["clarifications"] = []; requests["requests"] = []
    assert any("no clarification" in e for e in validate_outputs(p2, requests, record, store))


def test_unsupported_statement_is_absent_from_record_and_summary(tmp_path):
    class Unsupported(FixtureClient):
        def create(self, **kw):
            response = super().create(**kw)
            if kw["tool_choice"].get("name") == "assess_item":
                payload = response.content[0].input
                payload["checks"]["statement"].update(result="fail", reason="In-meeting procedure")
                payload["next_action"] = "stop"
            return response
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", LLM(Unsupported(), model="offline"), tmp_path, tmp_path / "cache")
    assert result["status"] == "COMPLETED" and result["quarantined"] == 1
    assert json.loads(Path(result["output"]).read_text(encoding="utf8")) == []
    p2 = json.loads(Path(result["p2_artifact"]).read_text(encoding="utf8"))
    assert p2["items"][0]["record_id"] is None and not p2["items"][0]["recordable"]
    assert "abstract" not in result["stages"]


def test_check_and_lookup_model_turns_share_the_same_global_budget():
    store, it = MemoryStore(), item()
    first = assessment(store, it, "retrieve")
    first["checks"]["owner"].update(result="uncertain", quotes=[])
    class Transport:
        p2_legacy_input = True
        def __init__(self):
            self.calls = 0
            self.messages = NS(create=self.create)
        def create(self, **kw):
            self.calls += 1
            return NS(content=[NS(type="tool_use", id="budgeted", name="assess_item", input=first)],
                      usage=NS(input_tokens=10, output_tokens=5))
    client, control, states = Transport(), RunControl(Limits(1, 1000000, 1, 5, 2, 0, 0)), []
    try:
        with pytest.raises(RunStopped, match="call_budget_exhausted"):
            run_p2_agent(LLM(client).scoped(control), store, it, MeetingBudget(), Trace(), CUR + "#0", states.append)
        assert client.calls == control.snapshot()["attempted_calls"] == 1
        assert states[-1]["checks"]["owner"]["result"] == "uncertain"
        assert states[-1]["history"][-1]["decision"] == "retrieve"
    finally:
        control.seal()


def test_sidecar_publication_failure_rolls_back_all_four_canonical_files(tmp_path, monkeypatch):
    path = config.TRANSCRIPTS / "TS3006a.txt"
    first = pipeline.run_meeting(path, "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    paths = [Path(first[k]) for k in ("output", "p2_artifact", "clarifications_artifact")]
    paths.append(tmp_path / "s1/TS3006a_report.md")
    original_bytes = {p: p.read_bytes() for p in paths}
    write = observation.write_text
    def fail(path, text, control=None):
        if path == Path(first["clarifications_artifact"]):
            raise OSError("cannot publish clarification sidecar")
        return write(path, text, control)
    monkeypatch.setattr(observation, "write_text", fail)
    result = pipeline.run_meeting(path, "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "FAILED" and result["failed_stage"] == "publish"
    assert {p: p.read_bytes() for p in paths} == original_bytes
    assert Path(result["p2_partial_artifact"]).exists() and Path(result["clarifications_partial_artifact"]).exists()


def test_timeout_keeps_interrupted_check_snapshot_and_fences_late_response(tmp_path, monkeypatch):
    path = config.TRANSCRIPTS / "TS3006a.txt"
    first = pipeline.run_meeting(path, "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    before = Path(first["output"]).read_bytes()
    release = threading.Event()
    class Slow(FixtureClient):
        def create(self, **kw):
            if kw["tool_choice"].get("name") == "assess_item":
                release.wait(2)
            return super().create(**kw)
    monkeypatch.setattr(config, "REQUEST_TIMEOUT", 0.04)
    try:
        result = pipeline.run_meeting(path, "s1", LLM(Slow(), model="offline-test-fixture"), tmp_path, tmp_path / "cache")
        assert result["status"] == "TIMED_OUT" and result["failed_stage"] == "p2_check"
        partial = json.loads(Path(result["p2_partial_artifact"]).read_text(encoding="utf8"))
        assert partial["items"][0]["state"] == "interrupted" and partial["published"] is False
        assert Path(first["output"]).read_bytes() == before
        files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        release.set(); time.sleep(0.08)
        assert files == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    finally:
        release.set()


@pytest.mark.parametrize("capture,index,expected", [("p2-v2", 1, "accepted"), ("p2-v3", 0, "pending")])
def test_captured_live_responses_replay_through_current_controller_without_api(capture, index, expected):
    folder = config.ROOT / "results/p2_smoke" / capture
    run = json.loads((folder / "run.json").read_text(encoding="utf8"))
    selected = json.loads((folder / "inputs.json").read_text(encoding="utf8"))
    it = selected[index]; it["run_id"] = run["run_id"]
    stage, steps = None, []
    for line in (folder / "stages.jsonl").read_text(encoding="utf8").splitlines():
        event = json.loads(line)
        if event.get("event") == "stage_started":
            stage = event["current_stage"]
        if stage == f"p2_item_{index}" and event.get("event") == "request_finished":
            assert len(event["response_tools"]) == 1
            tool = event["response_tools"][0]
            steps.append((tool["name"], tool["input"]))
    store = Store("TS3006a", allow_prior=False)
    flag_items([it], store)
    result = run_p2_agent(Script(steps), store, it, MeetingBudget(), Trace(), f"TS3006a#{index}")
    assert result["state"] == expected and result["recordable"] is True
    assert result["fields"] == run["items"][index]["fields"]
    if expected == "pending":
        assert result["termination_reason"] == "no_progress"
        assert [r["field"] for r in result["clarifications"]] == ["deadline"]
