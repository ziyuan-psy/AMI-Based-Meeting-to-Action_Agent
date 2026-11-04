"""Split/merge-tolerant matching: voting, duplicate rule and P/R (no API calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta.evaluate import drop_duplicates, make_tolerant_matcher, tolerant_prf, vote_links  # noqa: E402


class ScriptedJudge:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def forced_tool(self, *a, **k):
        self.calls += 1
        return {"links": self.answers.pop(0)}


def L(i, j, r):
    return {"sys": i, "gold": j, "relation": r}


def test_link_needs_majority_and_relation_is_voted():
    runs = [[L(0, 0, "same"), L(1, 0, "split"), L(2, 1, "merge")],
            [L(0, 0, "same"), L(1, 0, "same"), L(2, 2, "merge")],
            [L(0, 0, "split"), L(1, 0, "split"), L(9, 0, "same"), L(0, 1, "bogus")]]
    # (0,0): 3 votes, same 2 -> same. (1,0): 3 votes, same 1 -> split. (2,1), (2,2): 1 vote each -> dropped.
    assert vote_links(runs, 3, 3, 3) == [(0, 0, "same"), (1, 0, "split")]


def test_no_same_majority_falls_back_to_split_or_merge():
    runs = [[L(0, 0, "same")], [L(0, 0, "merge")], [L(0, 0, "merge")]]
    assert vote_links(runs, 1, 1, 3) == [(0, 0, "merge")]
    runs = [[L(0, 0, "same")], [L(0, 0, "split")], [L(0, 0, "merge")]]
    assert vote_links(runs, 1, 1, 3) == [(0, 0, "split")]  # tie between split and merge: split


def test_pure_duplicate_keeps_first_in_record_order():
    links = [("s2", "g1", "same"), ("s1", "g1", "same"), ("s3", "g1", "split")]
    kept, removed = drop_duplicates(links, ["s1", "s2", "s3"])
    assert removed == [("s2", "g1", "same")]
    assert sorted(kept) == [("s1", "g1", "same"), ("s3", "g1", "split")]


def test_precision_recall_tolerate_split_and_merge_but_not_duplicates():
    sys_ids, gold_ids = ["s1", "s2", "s3", "s4", "s5"], ["g1", "g2", "g3", "g4"]
    links = [("s1", "g1", "split"), ("s2", "g1", "split"),   # two complementary parts: both count
             ("s3", "g2", "merge"), ("s3", "g3", "merge"),   # one item covering two gold items
             ("s4", "g2", "same"), ("s5", "g2", "same")]     # s5 restates g2 again: false positive
    r = tolerant_prf(sys_ids, gold_ids, links)
    assert (r["linked_sys"], r["covered_gold"]) == (4, 3)
    assert (r["precision"], r["recall"]) == (0.8, 0.75)
    assert r["duplicate_links_removed"] == 1 and r["duplicates_counted_fp"] == 1
    assert r["relations"] == {"same": 1, "split": 2, "merge": 2}


def test_duplicate_with_another_link_still_counts_as_linked():
    r = tolerant_prf(["s1", "s2"], ["g1", "g2"],
                     [("s1", "g1", "same"), ("s2", "g1", "same"), ("s2", "g2", "split")])
    assert r["linked_sys"] == 2 and r["duplicates_counted_fp"] == 0


def test_matcher_votes_caches_and_maps_ids():
    ss = [{"id": "a", "text": "Buy parts"}, {"id": "b", "text": "Check costs"}]
    gg = [{"id": "x", "text": "Buy parts and check costs"}]
    judge = ScriptedJudge([[L(0, 0, "split"), L(1, 0, "split")]] * 2 + [[L(0, 0, "split")]])
    cache = {}
    m = make_tolerant_matcher(judge, cache, votes=3)
    assert m(ss, gg) == [("a", "x", "split"), ("b", "x", "split")]
    assert m(ss, gg) == [("a", "x", "split"), ("b", "x", "split")] and judge.calls == 3  # second call from cache
    assert m([], gg) == [] and m(ss, []) == []
