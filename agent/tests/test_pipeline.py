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


def use(name, inp, i=1):
    return NS(type="tool_use", id=f"tu{i}", name=name, input=inp)


class Fake:
    """Answers extraction, agent and abstract calls from a script; counts calls per tool."""

    def __init__(self, agent_script):
        self.agent_script = list(agent_script)
        self.agent_calls = []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        choice = (kw.get("tool_choice") or {}).get("name")
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
        step = self.agent_script.pop(0)
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


def test_b0_uses_no_tools(tmp_path):
    fb = Fake([])
    r = run_meeting(B, "b0", LLM(fb), tmp_path, tmp_path / "c")
    assert r["status"] == "COMPLETED" and fb.agent_calls == []
