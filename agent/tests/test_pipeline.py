"""Offline tests with a scripted fake model: plumbing, limits and visibility rules (no API calls)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import config  # noqa: E402
from mta.llm import LLM  # noqa: E402
from mta.pipeline import run_meeting  # noqa: E402
from mta.store import Store  # noqa: E402

A, B = config.TRANSCRIPTS / "TS3006a.txt", config.TRANSCRIPTS / "TS3006b.txt"


@pytest.fixture(autouse=True)
def legacy_protocol_ablation(monkeypatch):
    """These scripted conversations specify the original agent protocol; P2 has its own protocol tests."""
    monkeypatch.setattr(config, "P2_ENABLED", False)


def use(name, inp, i=1):
    return NS(type="tool_use", id=f"tu{i}", name=name, input=inp)


class Fake:
    """Answers extraction, agent and abstract calls from a script; counts calls per tool."""

    def __init__(self, agent_script):
        self.agent_script = list(agent_script)
        self.agent_calls = []
        self.n_calls = 0
        self.efforts = {}  # tool_choice name (or "agent") -> reasoning efforts seen
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.n_calls += 1
        choice = (kw.get("tool_choice") or {}).get("name")
        key = choice if choice in ("submit_candidates", "submit_final_items", "submit_abstracts", "select_record_items") else "agent"
        self.efforts.setdefault(key, set()).add(kw.get("reasoning_effort"))
        usage = NS(input_tokens=10, output_tokens=5)
        if choice == "submit_candidates":
            user = kw["messages"][0]["content"]
            if "List ONLY actions" not in user:  # chunks must not be asked for actions; only the global pass is
                return NS(usage=usage, content=[use(choice, {"items": []})])
            first = int(user.split("Transcript:")[1].split("[")[1].split("]")[0])
            text = "The user interface designer will develop the remote layout and LCD menu."
            return NS(usage=usage, content=[use(choice, {"items": [{
                "type": "actions", "text": text, "evidence": [first, first + 1], "owner_ids": ["MTD023UID"],
                "owner_cue": "UI", "owner_basis": "explicit", "deadline": None, "deadline_basis": "missing",
                "status": "in_progress"}]})])
        if choice == "submit_final_items":
            return NS(usage=usage, content=[use(choice, {"items": [{
                "type": "actions", "text": "The user interface designer will develop the remote layout and LCD menu.",
                "members": [0]}]})])
        if choice == "submit_abstracts":
            return NS(usage=usage, content=[use(choice, {"abstracts": [{"text": "Layout work was assigned.", "items": [0]}]})])
        if choice == "select_record_items":
            data = json.loads(kw["messages"][0]["content"])
            return NS(usage=usage, content=[use(choice, {key:{'keep':True,'claims':[{'text':c['text'],'priority':3}],
                'reason':'Substantive fixture task'} for key,c in data['candidates'].items()})])
        step = self.agent_script.pop(0)
        if step is None:  # the model answers without any tool call (e.g. it ran out of output budget)
            self.agent_calls.append(None)
            return NS(usage=usage, content=[])
        self.agent_calls.append(step[0])
        return NS(usage=usage, content=[use(step[0], step[1](kw) if callable(step[1]) else step[1])])


def finish_with_prior(kw):
    last = kw["messages"][-1]["content"][0]["content"]
    prior_id = json.loads(last)["results"][0]["id"]
    da = Store("TS3006b").meeting("TS3006b", B).acts[3]["id"]
    return {"owner": ["MTD023UID"], "owner_basis": "explicit", "deadline": None, "deadline_basis": "missing",
            "status": "in_progress", "prior_context_needed": True,
            "related_items": [{"id": prior_id, "relation": "updates"}], "evidence_da_ids": [da]}


def test_series_flow_links_to_earlier_meeting(tmp_path):
    fa = Fake([])
    ra = run_meeting(A, "s2", LLM(fa), tmp_path, tmp_path / "c")
    assert ra["status"] == "COMPLETED" and fa.agent_calls == []  # explicit owner, order 1: no agent needed

    fb = Fake([("get_prior_items", {"query": "remote layout LCD menu"}), ("finish", finish_with_prior)])
    rb = run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    assert rb["status"] == "COMPLETED", rb
    rec = json.loads((tmp_path / "s2" / "TS3006b.json").read_text(encoding="utf-8"))
    action = next(r for r in rec if r["type"] == "actions")
    assert action["related_items"][0]["relation"] == "updates"
    assert action["related_items"][0]["id"].startswith("TS3006a.sys.s.")
    assert fb.agent_calls == ["get_prior_items", "finish"]
    assert (tmp_path / "s2" / "TS3006b_report.md").exists()
    trace = [json.loads(l) for l in (tmp_path / "s2" / "TS3006b.trace.jsonl").read_text().splitlines()]
    assert [t["action"] for t in trace] == ["get_prior_items", "finish"]


def test_s1_cannot_see_earlier_meetings(tmp_path):
    s = Store("TS3006b", allow_prior=False, run_dir=tmp_path)
    assert s.visible_ids() == ["TS3006b"]
    assert s.search_dialogue("layout", "prior") == []
    with pytest.raises(PermissionError):
        s.read_dialogue("TS3006a", "TS3006a.A.dialog-act.dharshi.1")


def test_future_and_other_series_are_unreadable():
    s = Store("TS3006b", allow_prior=True)
    for other in ("TS3006c", "TS3006d", "ES2011a"):
        with pytest.raises(PermissionError):
            s.roster(other)


def test_budget_forces_finish_and_falls_back(tmp_path):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    # agent keeps searching with the same query: no new info -> forced finish -> invalid -> fallback to defaults
    loop = [("search_dialogue", {"query": "layout", "scope": "prior"})] * 2 + [("finish", {
        "status": "bogus", "prior_context_needed": False, "related_items": [], "evidence_da_ids": ["nope"]})] * 3
    fb = Fake(loop)
    rb = run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    assert rb["status"] == "COMPLETED" and rb["agent fallbacks"] == 1
    assert fb.agent_calls.count("search_dialogue") <= config.MAX_CALLS_PER_ITEM


def read_trace(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def test_no_tool_call_under_forced_finish_terminates(tmp_path):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    # The model never calls a tool, not even when finish is forced: the loop must still stop.
    fb = Fake([None] * 50)
    rb = run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    assert rb["status"] == "COMPLETED" and rb["agent fallbacks"] == 1
    # 2 empty replies make it forced, then MAX_FINISH_TRIES empty forced replies end it
    assert len(fb.agent_calls) == config.MAX_INVALID_ACTIONS + config.MAX_FINISH_TRIES
    trace = read_trace(tmp_path / "s2" / "TS3006b.trace.jsonl")
    assert [t["outcome"] for t in trace][-1] == "gave_up"
    assert sum(t["outcome"] == "no_tool_call" for t in trace) == len(fb.agent_calls)


def test_step_limit_stops_agent(tmp_path, monkeypatch):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    monkeypatch.setattr(config, "MAX_AGENT_STEPS", 3)
    monkeypatch.setattr(config, "MAX_INVALID_ACTIONS", 99)  # only the step limit can end this loop
    fb = Fake([None] * 50)
    rb = run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    assert rb["agent fallbacks"] == 1 and len(fb.agent_calls) == 3
    last = read_trace(tmp_path / "s2" / "TS3006b.trace.jsonl")[-1]
    assert last["outcome"] == "gave_up" and last["reason"] == "step limit"


def test_tool_error_is_traced_as_error(tmp_path):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    # TS3006c is a later meeting, so reading it from TS3006b is refused
    bad = ("read_dialogue", {"meeting_id": "TS3006c", "da_id": "TS3006c.A.dialog-act.dharshi.1"})
    fb = Fake([bad] + [("finish", {"status": "bogus", "prior_context_needed": False, "related_items": [],
                                   "evidence_da_ids": ["nope"]})] * 3)
    run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    first = read_trace(tmp_path / "s2" / "TS3006b.trace.jsonl")[0]
    assert first["action"] == "read_dialogue"
    assert first["outcome"] == "tool_error" and first["error"].startswith("PermissionError")


def test_token_counts_are_per_meeting(tmp_path):
    fake_a = Fake([])
    llm = LLM(fake_a)
    ra = run_meeting(A, "s2", llm, tmp_path, tmp_path / "c")
    fake_b = Fake([])
    llm.client = fake_b  # same LLM object, as in a series run; fresh fake to count meeting B's calls
    rb = run_meeting(B, "s1", llm, tmp_path, tmp_path / "c")
    assert fake_a.n_calls > 0 and fake_b.n_calls > 0
    assert ra["input tokens"] == 10 * fake_a.n_calls
    assert rb["input tokens"] == 10 * fake_b.n_calls and rb["output tokens"] == 5 * fake_b.n_calls
    assert llm.in_tokens == ra["input tokens"] + rb["input tokens"]


class FlakyActions(Fake):
    """The whole-meeting action pass answers with an empty list the first `empties` times."""

    def __init__(self, agent_script, empties):
        super().__init__(agent_script)
        self.empties, self.action_calls = empties, 0

    def create(self, **kw):
        if (kw.get("tool_choice") or {}).get("name") == "submit_candidates" and \
                "List ONLY actions" in kw["messages"][0]["content"]:
            self.action_calls += 1
            if self.action_calls <= self.empties:
                self.n_calls += 1
                return NS(usage=NS(input_tokens=10, output_tokens=5), content=[use("submit_candidates", {"items": []})])
        return super().create(**kw)


def test_empty_action_pass_is_retried(tmp_path):
    f = FlakyActions([], empties=1)
    r = run_meeting(A, "s1", LLM(f), tmp_path, tmp_path / "c")
    assert r["status"] == "COMPLETED" and r["items"] == 1  # the two samples agree: one action after the union
    assert f.action_calls == 1 + config.ACTION_SAMPLES  # the empty sample was asked once more


def test_empty_extraction_is_not_cached(tmp_path):
    f = FlakyActions([], empties=99)  # always empty
    run_meeting(A, "s1", LLM(f), tmp_path, tmp_path / "c")
    assert f.action_calls == config.ACTION_SAMPLES * (1 + config.EMPTY_RETRIES)
    saved = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "c").glob("extract_*.json")]
    assert all("action_pass" not in s for s in saved)  # empty action samples are never cached
    # a later run must call the model again instead of reusing an empty result
    f2 = Fake([])
    r2 = run_meeting(A, "s1", LLM(f2), tmp_path, tmp_path / "c")
    assert r2["items"] == 1


def test_reasoning_effort_per_call_type(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EFFORT_EXTRACT", None)
    monkeypatch.setattr(config, "EFFORT_AGENT", "low")
    monkeypatch.setattr(config, "EFFORT_ABSTRACT", "minimal")
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    fb = Fake([("get_prior_items", {"query": "remote layout LCD menu"}), ("finish", finish_with_prior)])
    llm = LLM(fb)
    llm.supports_effort = True  # behave like the OpenAI adapter
    run_meeting(B, "s2", llm, tmp_path, tmp_path / "c")
    assert fb.efforts["submit_candidates"] == {None}
    assert fb.efforts["agent"] == {"low"}
    assert fb.efforts["submit_abstracts"] == {"minimal"}
    # clients without the parameter (Anthropic, fakes) never receive it
    fc = Fake([("get_prior_items", {"query": "remote layout LCD menu"}), ("finish", finish_with_prior)])
    run_meeting(B, "s2", LLM(fc), tmp_path, tmp_path / "c")
    assert all(v == {None} for v in fc.efforts.values())


def test_meeting_budget_is_exact_under_threads():
    from concurrent.futures import ThreadPoolExecutor
    from mta.agent import MeetingBudget
    b = MeetingBudget()
    with ThreadPoolExecutor(16) as pool:
        got = list(pool.map(lambda _: b.take(), range(200)))
    assert sum(got) == config.MAX_CALLS_PER_MEETING == b.used


def test_abstract_refs_come_from_agent_fields(tmp_path):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    fb = Fake([("get_prior_items", {"query": "remote layout LCD menu"}), ("finish", finish_with_prior)])
    run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    rec = json.loads((tmp_path / "s2" / "TS3006b.json").read_text(encoding="utf-8"))
    action = next(r for r in rec if r["type"] == "actions")
    abstract = next(r for r in rec if r["type"] == "abstract")
    assert abstract["reference"] == action["reference"]  # drafted in parallel, references attached after the agent


def test_empty_reply_leaves_no_empty_assistant_message(tmp_path):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    seen = []

    def finish_after_check(kw):
        seen.extend(m for m in kw["messages"] if m["role"] == "assistant")
        return finish_with_prior({"messages": [{"content": [{"content": json.dumps(
            {"results": [{"id": Store("TS3006b", run_dir=tmp_path / "s2").prior_items()[0]["id"]}]})}]}]})

    fb = Fake([None, ("finish", finish_after_check)])
    rb = run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    assert rb["agent fallbacks"] == 0
    assert seen and all(m["content"] for m in seen)  # the empty reply was replaced by a placeholder
    from mta.llm import OpenAIAdapter
    converted = OpenAIAdapter._messages("sys", [{"role": "assistant", "content": seen[0]["content"]}])
    assert converted[1]["content"]  # no null content goes to the OpenAI API


def test_agent_api_exception_stops_meeting_and_preserves_partial_results(tmp_path):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")

    def boom(kw):
        raise RuntimeError("API down")

    rb = run_meeting(B, "s2", LLM(Fake([("finish", boom)])), tmp_path, tmp_path / "c")
    assert rb["status"] == "FAILED" and rb["failed_stage"] == "agent"
    assert rb["reason"] == "model_request_failed"
    assert Path(rb["partial_artifact"]).is_file()
    assert not (tmp_path / "s2" / "TS3006b.json").exists()


def test_stage_usage_adds_up(tmp_path):
    run_meeting(A, "s2", LLM(Fake([])), tmp_path, tmp_path / "c")
    fb = Fake([("get_prior_items", {"query": "remote layout LCD menu"}), ("finish", finish_with_prior)])
    rb = run_meeting(B, "s2", LLM(fb), tmp_path, tmp_path / "c")
    st = rb["stages"]
    assert {"extract", "agent", "abstract"} <= set(st) and "other" not in st
    assert st["agent"]["calls"] == 2 and st["abstract"]["calls"] == 1
    assert sum(s["calls"] for s in st.values()) == rb["llm calls"] == fb.n_calls
    assert sum(s["in"] for s in st.values()) == rb["input tokens"]
    assert {"extract", "agent+abstract", "total"} <= set(rb["stage_seconds"])
    # a rerun that hits the extraction cache spends nothing on extraction
    fc = Fake([("get_prior_items", {"query": "remote layout LCD menu"}), ("finish", finish_with_prior)])
    rc = run_meeting(B, "s2", LLM(fc), tmp_path, tmp_path / "c")
    assert "extract" not in rc["stages"]


def test_b0_uses_no_tools(tmp_path):
    fb = Fake([])
    r = run_meeting(B, "b0", LLM(fb), tmp_path, tmp_path / "c")
    assert r["status"] == "COMPLETED" and fb.agent_calls == []
