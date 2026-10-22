"""Offline tests for the Claude evaluation judge (fake Anthropic client, no API calls)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import config  # noqa: E402
from mta.evaluate import MATCH_SCHEMA, make_llm_matcher  # noqa: E402
from mta.judge import ClaudeJudge, strict_schema  # noqa: E402
from mta.support import SCHEMA as SUPPORT_SCHEMA  # noqa: E402


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class FakeAnthropic:
    """Records each request; answers from a list of (stop_reason, text) pairs."""

    def __init__(self, answers):
        self.answers, self.requests = list(answers), []
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kw):
        self.requests.append(kw)
        stop, text = self.answers.pop(0)
        return FakeStream(NS(stop_reason=stop, stop_details=NS(category="cyber"),
                             content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
                             usage=NS(input_tokens=100, output_tokens=20)))


def test_request_uses_claude_structured_output_not_forced_tools():
    fake = FakeAnthropic([("end_turn", json.dumps({"pairs": [{"sys": 0, "gold": 1}]}))])
    j = ClaudeJudge(client=fake)
    out = j.forced_tool("sys", "user", "submit_matches", MATCH_SCHEMA)
    assert out == {"pairs": [{"sys": 0, "gold": 1}]}
    req = fake.requests[0]
    assert req["model"] == config.JUDGE_MODEL == "claude-opus-5-5"
    assert "tool_choice" not in req and "tools" not in req
    assert req["output_config"]["effort"] == "high"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert j.calls == 1 and j.in_tokens == 100 and j.out_tokens == 20


def test_strict_schema_closes_every_object():
    s = strict_schema(SUPPORT_SCHEMA)
    assert s["additionalProperties"] is False and s["required"] == ["verdicts"]
    item = s["properties"]["verdicts"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])  # the optional "note" becomes required
    bounded = strict_schema({"type": "object", "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5}}})
    assert bounded["properties"]["score"] == {"type": "integer"}
    assert "additionalProperties" not in SUPPORT_SCHEMA  # the original is not modified


def test_refusal_raises_and_truncation_is_retried():
    with pytest.raises(RuntimeError, match="refused"):
        ClaudeJudge(client=FakeAnthropic([("refusal", "")])).forced_tool("s", "u", "x", MATCH_SCHEMA)
    fake = FakeAnthropic([("max_tokens", '{"pairs": ['), ("end_turn", '{"pairs": []}')])
    assert ClaudeJudge(client=fake).forced_tool("s", "u", "x", MATCH_SCHEMA) == {"pairs": []}
    assert len(fake.requests) == 2
    with pytest.raises(RuntimeError, match="no usable answer"):
        ClaudeJudge(client=FakeAnthropic([("end_turn", "not json")] * 2)).forced_tool("s", "u", "x", MATCH_SCHEMA)


def test_default_judge_is_the_pipeline_model_with_original_cache_names(tmp_path, monkeypatch):
    import mta.llm
    from mta.judge import cache_path, get_judge

    class PipelineLLM:  # stands in for mta.llm.LLM (no client, no key needed)
        model = "pipeline-model"

    monkeypatch.setattr(mta.llm, "LLM", PipelineLLM)
    monkeypatch.setattr(config, "JUDGE", "pipeline")
    j = get_judge()
    assert isinstance(j, PipelineLLM)
    assert cache_path(tmp_path, "align_cache", j).name == "align_cache.json"
    monkeypatch.setattr(config, "JUDGE", "claude")
    claude = ClaudeJudge(client=FakeAnthropic([]))
    assert cache_path(tmp_path, "support_cache", claude).name == "support_cache_claude-opus-5-5.json"


def test_matcher_works_with_the_judge_and_caches_per_model(tmp_path):
    from mta.judge import JsonCache, cache_path
    fake = FakeAnthropic([("end_turn", json.dumps({"pairs": [{"sys": 0, "gold": 0}]}))])
    j = ClaudeJudge(client=fake)
    path = cache_path(tmp_path, "align_votes", j)
    m = make_llm_matcher(j, JsonCache.get(path), votes=1)
    ss, gg = [{"id": "s1", "text": "Do X"}], [{"id": "g1", "text": "X will be done"}]
    assert m(ss, gg) == {"s1": "g1"}
    assert m(ss, gg) == {"s1": "g1"} and len(fake.requests) == 1  # second call served from the cache
    assert path.name == "align_votes_claude-opus-5-5.json" and path.exists()


class ScriptedJudge:
    """Returns the next alignment from a list on each call."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def forced_tool(self, *a, **k):
        self.calls += 1
        return {"pairs": self.answers.pop(0)}


def test_alignment_is_a_majority_vote_and_one_to_one():
    ss = [{"id": f"s{i}", "text": f"sys {i}"} for i in range(3)]
    gg = [{"id": f"g{j}", "text": f"gold {j}"} for j in range(3)]
    judge = ScriptedJudge([
        [{"sys": 0, "gold": 0}, {"sys": 1, "gold": 1}, {"sys": 2, "gold": 1}],
        [{"sys": 0, "gold": 0}, {"sys": 2, "gold": 1}],
        [{"sys": 0, "gold": 0}, {"sys": 1, "gold": 2}, {"sys": 2, "gold": 1}, {"sys": 9, "gold": 0}],
    ])
    m = make_llm_matcher(judge, {}, votes=3)
    # (0,0): 3 votes; (2,1): 3 votes; (1,1) and (1,2): one vote each -> rejected; (9,0) out of range -> ignored
    assert m(ss, gg) == {"s0": "g0", "s2": "g1"}
    assert judge.calls == 3


def test_identical_lists_share_one_alignment_and_extra_votes_are_added_lazily():
    ss, gg = [{"id": "a", "text": "same text"}], [{"id": "g", "text": "gold"}]
    store = {}
    j1 = ScriptedJudge([[{"sys": 0, "gold": 0}]] * 3)
    assert make_llm_matcher(j1, store, votes=1)(ss, gg) == {"a": "g"} and j1.calls == 1
    # another run / mode with the same texts (different ids) reuses the stored vote
    j2 = ScriptedJudge([[{"sys": 0, "gold": 0}]] * 3)
    assert make_llm_matcher(j2, store, votes=1)([{"id": "b", "text": "same text"}], gg) == {"b": "g"}
    assert j2.calls == 0
    # raising the vote count only runs the missing judgements
    j3 = ScriptedJudge([[], []])
    assert make_llm_matcher(j3, store, votes=3)(ss, gg) == {}  # 1 of 3 votes is not a majority
    assert j3.calls == 2


def test_parallel_requests_for_the_same_lists_share_one_judgement(tmp_path):
    import random
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from mta.judge import JsonCache

    class SlowRandomJudge:
        """Slow, and gives a different answer on every call: without the per-key lock, two threads would each
        judge and each keep their own answer."""

        def __init__(self):
            self.calls = 0
            self._lock = threading.Lock()

        def forced_tool(self, *a, **k):
            with self._lock:
                self.calls += 1
            time.sleep(0.05)
            return {"pairs": [{"sys": 0, "gold": random.randrange(3)}]}

    ss = [{"id": "s", "text": "same"}]
    gg = [{"id": f"g{j}", "text": f"gold {j}"} for j in range(3)]
    judge, store = SlowRandomJudge(), JsonCache.get(tmp_path / "votes.json")
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: make_llm_matcher(judge, store, votes=3)(ss, gg), range(4)))
    assert judge.calls == 3
    assert all(r == results[0] for r in results)


def test_json_cache_is_shared_and_survives_reload(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from mta.judge import JsonCache
    path = tmp_path / "c.json"
    c = JsonCache.get(path)
    assert JsonCache.get(path) is c
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: c.__setitem__(f"k{i}", i), range(50)))
    assert json.loads(path.read_text(encoding="utf-8")) == {f"k{i}": i for i in range(50)}
