"""Code-side rules applied to the consolidation output (no API calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import config  # noqa: E402
from mta.extract import _merge_groups, _strip_indexes  # noqa: E402


def cand(typ, text, ev, owner=None):
    return {"type": typ, "text": text, "evidence_da_ids": [ev], "owner_ids": [owner] if owner else [],
            "owner_cue": None, "deadline_cue": None, "owner_basis": "explicit" if owner else "missing",
            "deadline": None, "deadline_basis": "missing", "status": "open" if typ == "actions" else "adopted"}


def test_candidate_indexes_are_removed_from_text():
    assert _strip_indexes("Assign owners and set next steps. [93,89,90]") == "Assign owners and set next steps."
    assert _strip_indexes("Use [12, 15] the yellow case [3]") == "Use the yellow case"
    assert _strip_indexes("Target group aged 19-35 (young professionals)") == "Target group aged 19-35 (young professionals)"
    out = _merge_groups([{"type": "decisions", "text": "Batteries will be used. [0]", "members": [0]}],
                        [cand("decisions", "batteries", "m.A.1")])
    assert out[0]["text"] == "Batteries will be used."


def test_abstract_citations_are_removed_from_text():
    from types import SimpleNamespace as NS
    from mta.pipeline import draft_abstracts

    class OneCall:
        def forced_tool(self, *a, **k):
            return {"abstracts": [{"text": "LEDs will be indicators and the interface stays one level. [4],[5]",
                                   "items": [0]}, {"text": "[2]", "items": [0]}]}

    out = draft_abstracts(OneCall(), [{"type": "decisions", "text": "x"}])
    assert [a["text"] for a in out] == ["LEDs will be indicators and the interface stays one level."]


def test_group_without_action_members_cannot_become_an_action():
    cands = [cand("decisions", "hatch door", "m.A.1"), cand("decisions", "hatch", "m.A.2"),
             cand("actions", "ID will draw the case", "m.B.3", owner="P1")]
    out = _merge_groups([{"type": "actions", "text": "The team will use a hatch door.", "members": [0, 1]},
                         {"type": "actions", "text": "The ID will draw the case.", "members": [2]},
                         {"type": "decisions", "text": "We will use a hatch door.", "members": [2]}], cands)
    assert [o["type"] for o in out] == ["decisions", "actions", "decisions"]  # action -> decision is allowed
    assert out[1]["owner_ids"] == ["P1"]


def test_dropped_actions_are_restored_within_the_cap(monkeypatch):
    from mta.extract import restore_actions
    monkeypatch.setattr(config, "MAX_ITEMS", {"actions": 3, "decisions": 7, "problems": 4})
    a1 = cand("actions", "ME will research trends [4]", "m.A.1", owner="P1")
    a2 = cand("actions", "ID will draw the case", "m.B.2", owner="P2")
    a2_again = cand("actions", "ID draws the case", "m.B.2", owner="P2")  # same lines as a2
    a3 = cand("actions", "PM sends minutes", "m.C.3", owner="P3")
    a4 = cand("actions", "UI makes a mock-up", "m.D.4", owner="P4")
    d = cand("decisions", "batteries", "m.A.9")
    kept = [{**a3, "text": "The PM will send the minutes.", "evidence_da_ids": ["m.C.3", "m.C.5"]},
            {**d, "text": "Batteries will be used."}]
    out = restore_actions(kept, [a1, a2, a2_again, a3, a4, d])
    actions = [o for o in out if o["type"] == "actions"]
    # a3 is absorbed by the kept action; a1, a2 come back in order (a2 once); a4 is cut by the cap of 3
    assert [o["text"] for o in actions] == ["The PM will send the minutes.", "ME will research trends", "ID will draw the case"]
    assert [o.get("restored", False) for o in actions] == [False, True, True]
    assert sum(o["type"] == "decisions" for o in out) == 1  # other types are never restored


def test_restore_also_runs_on_cached_consolidation(tmp_path):
    import json
    from mta.extract import consolidate
    a = cand("actions", "ID will draw the case", "m.B.2", owner="P2")
    d = cand("decisions", "batteries", "m.A.9")

    class NoCall:
        model = "fake"

        def forced_tool(self, *a, **k):
            return {"items": [{"type": "decisions", "text": "Batteries will be used.", "members": [1]}]}

    llm = NoCall()
    cache = tmp_path / "consolidate_x.json"
    first = consolidate(llm, [a, d], cache)
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert len(saved["runs"]) == config.CONSOLIDATE_SAMPLES
    assert [i["type"] for i in saved["runs"][0]] == ["decisions"]  # the cache keeps the model's own result
    assert [i["type"] for i in first] == ["decisions", "actions"]

    class Boom(NoCall):
        def forced_tool(self, *a, **k):
            raise AssertionError("must be served from the cache")

    assert [i["type"] for i in consolidate(Boom(), [a, d], cache)] == ["decisions", "actions"]


def test_union_of_action_samples_drops_repeats():
    from mta.extract import union_actions
    s1 = [cand("actions", "The ID will draw the case", "m.A.1", owner="P1")]
    s2 = [cand("actions", "ID draws the case design", "m.A.1", owner="P1"),     # same line -> repeat
          cand("actions", "The ID will draw the case", "m.Z.9", owner="P1"),    # same words -> repeat
          cand("actions", "The ME will research trends", "m.B.2", owner="P2")]  # new
    assert [c["text"] for c in union_actions([s1, s2])] == ["The ID will draw the case", "The ME will research trends"]


def test_vote_keeps_items_a_majority_of_runs_agree_on():
    from mta.extract import vote_items
    d = lambda text, ev: {**cand("decisions", text, ev[0]), "text": text, "evidence_da_ids": ev}
    run1 = [d("A", ["l1", "l2"]), d("B", ["l5"]), d("C", ["l9"])]
    run2 = [d("B'", ["l5", "l6"]), d("A'", ["l1"]), d("D", ["l7"])]
    run3 = [d("A''", ["l2", "l3"]), d("E", ["l8"])]
    out = vote_items([run1, run2, run3])
    # A in 3 runs, B in 2 -> kept (earliest wording); C, D, E in one run each -> dropped
    assert [o["text"] for o in out] == ["A", "B"]


def test_consolidation_samples_are_cached_and_topped_up(tmp_path, monkeypatch):
    import json
    from mta.extract import consolidate
    cands = [cand("decisions", "batteries", "m.A.9"), cand("decisions", "yellow case", "m.A.10")]

    class Counting:
        model = "fake"

        def __init__(self):
            self.calls = 0

        def forced_tool(self, *a, **k):
            self.calls += 1
            return {"items": [{"type": "decisions", "text": "Batteries.", "members": [0]},
                              {"type": "decisions", "text": "Yellow case.", "members": [1]}]}

    cache = tmp_path / "consolidate_x.json"
    monkeypatch.setattr(config, "CONSOLIDATE_SAMPLES", 1)
    c1 = Counting()
    consolidate(c1, cands, cache)
    assert c1.calls == 1
    monkeypatch.setattr(config, "CONSOLIDATE_SAMPLES", 3)
    c2 = Counting()
    out = consolidate(c2, cands, cache)
    assert c2.calls == 2 and len(json.loads(cache.read_text(encoding="utf-8"))["runs"]) == 3
    assert [o["text"] for o in out] == ["Batteries.", "Yellow case."]


def test_chunk_pass_is_reused_when_only_the_action_pass_changes(tmp_path, monkeypatch):
    import mta.extract as ex
    from types import SimpleNamespace as NS
    acts = [{"id": f"m.A.{i}", "text": f"line {i}", "attributes": {"role": "PM", "participant": "P1"}} for i in range(5)]
    roster = [{"participant_id": "P1", "role": "PM"}]

    class Calls:
        model = "fake"

        def __init__(self):
            self.notes = []

        def forced_tool(self, system, user, *a, **k):
            if "List ONLY actions" in user:
                self.notes.append("action")
                return {"items": [{"type": "actions", "text": "PM sends minutes", "evidence": [4], "owner_ids": ["P1"],
                                   "owner_basis": "explicit", "deadline": None, "deadline_basis": "missing", "status": "open"}]}
            self.notes.append("chunk")
            return {"items": [{"type": "decisions", "text": "Use batteries", "evidence": [1], "owner_ids": [],
                               "owner_basis": "missing", "deadline": None, "deadline_basis": "missing", "status": "adopted"}]}

    cache = tmp_path / "extract_False_m.json"
    first = Calls()
    out = ex.extract_candidates(first, acts, roster, cache=cache)
    assert sorted(first.notes) == ["action"] * config.ACTION_SAMPLES + ["chunk"]
    assert [c["type"] for c in out] == ["decisions", "actions"]  # identical samples are united into one action
    monkeypatch.setattr(ex, "ACTION_PASS_NOTE", ex.ACTION_PASS_NOTE + " (new wording)")
    second = Calls()
    ex.extract_candidates(second, acts, roster, cache=cache)
    assert second.notes == ["action"] * config.ACTION_SAMPLES  # the chunk pass came from the cache
    third = Calls()
    ex.extract_candidates(third, acts, roster, cache=cache)
    assert third.notes == []  # everything cached now


def test_caps_keep_the_first_items_of_each_type(monkeypatch):
    monkeypatch.setattr(config, "MAX_ITEMS", {"actions": 1, "decisions": 2, "problems": 0})
    cands = [cand("decisions", f"d{i}", f"m.A.{i}") for i in range(4)] + [cand("actions", "a", "m.B.9", owner="P1"),
                                                                           cand("problems", "p", "m.C.8")]
    groups = [{"type": "decisions", "text": f"D{i}", "members": [i]} for i in range(4)]
    groups += [{"type": "actions", "text": "A1", "members": [4]}, {"type": "actions", "text": "A2", "members": [4]},
               {"type": "problems", "text": "P", "members": [5]}]
    out = _merge_groups(groups, cands)
    assert [o["text"] for o in out] == ["D0", "D1", "A1"]
