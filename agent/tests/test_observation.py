"""Stage lineage, cache provenance and immutable baseline captures; no model API calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import baseline, config  # noqa: E402
from mta.extract import consolidate, extract_candidates  # noqa: E402
from mta.llm import LLM  # noqa: E402
from mta.observation import runtime_identity, sha256  # noqa: E402
from mta.pipeline import run_meeting  # noqa: E402
from mta.trace import StageTrace  # noqa: E402


def candidate(index):
    return {"type": "decisions", "text": f"Choice {index}", "evidence_da_ids": [f"m.A.{index}"],
            "owner_ids": [], "owner_basis": "missing", "deadline": None, "deadline_basis": "missing",
            "status": "adopted"}


class Groups:
    model = "scripted-test-model"

    def __init__(self):
        self.answers = [[0, 1, 2], [0, 2, 1], [0, 3, 1]]

    def forced_tool(self, *args, **kwargs):
        return {"items": [{"type": "decisions", "text": f"Choice {i}", "members": [i]}
                          for i in self.answers.pop(0)]}


class NoCalls:
    model = Groups.model

    def forced_tool(self, *args, **kwargs):
        raise AssertionError("a cache hit must not call the model")


def test_stage_trace_freezes_snapshots_and_serializes_parallel_events(tmp_path):
    trace = StageTrace(tmp_path / "stages.jsonl", run_id="test")
    original = [{"text": "before"}]
    trace.log(stage="extract", event="completed", items=original)
    original[0]["text"] = "after"
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: trace.log(stage="test", event="parallel", index=i), range(40)))
    saved = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert saved == trace.events
    assert saved[0]["items"][0]["text"] == "before"
    assert [e["seq"] for e in saved] == list(range(41))


def test_consolidation_exposes_merges_caps_and_votes_without_changing_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKERS", 1)
    monkeypatch.setattr(config, "MAX_ITEMS", {"actions": 5, "decisions": 2, "problems": 4})
    cands, trace = [candidate(i) for i in range(4)], StageTrace()
    cached = tmp_path / "consolidate.json"
    observed = consolidate(Groups(), cands, cached, trace=trace)
    assert observed == consolidate(Groups(), cands)
    assert [c["text"] for c in observed] == ["Choice 0"]
    attempts = [e for e in trace.events if e["event"] == "attempt"]
    assert len(attempts) == 3
    assert attempts[0]["events"][0]["groups"][2]["members"] == [2]
    cap = next(e for e in attempts[0]["events"] if e["event"] == "cap")
    assert len(cap["before"]) == 3 and len(cap["kept"]) == 2
    assert cap["dropped"][0]["item"]["text"] == "Choice 2"
    vote = next(e for e in trace.events if e["event"] == "vote")
    assert vote["threshold"] == 2
    assert [c["votes"] for c in vote["clusters"]] == [3, 1, 1, 1]

    warm = StageTrace()
    assert consolidate(NoCalls(), cands, cached, trace=warm) == observed
    samples = [e for e in warm.events if e["event"] == "sample"]
    assert len(samples) == 3 and all(e["source"] == "cache" and e["detail_available"] for e in samples)
    assert samples[0]["recorded_attempts"][0]["events"] == attempts[0]["events"]

    legacy = json.loads(cached.read_text(encoding="utf-8"))
    legacy.pop("run_traces")
    cached.write_text(json.dumps(legacy), encoding="utf-8")
    old_trace = StageTrace()
    assert consolidate(NoCalls(), cands, cached, trace=old_trace) == observed
    assert all(not e["detail_available"] and e["recorded_attempts"] is None
               for e in old_trace.events if e["event"] == "sample")


def test_extraction_records_rejections_and_cache_hits(tmp_path):
    class Extraction(NoCalls):
        def forced_tool(self, *args, **kwargs):
            return {"items": [{"type": "decisions", "text": "Use batteries", "evidence": [0]},
                              {"type": "actions", "text": "Invalid evidence", "evidence": [99]},
                              {"type": "unknown", "text": "Wrong type", "evidence": [0]}]}

    acts = [{"id": "m.A.0", "text": "Use batteries", "attributes": {"role": "PM", "participant": "P1"}}]
    trace, cached = StageTrace(), tmp_path / "extract.json"
    first = extract_candidates(Extraction(), acts, [], single_chunk=True, cache=cached, trace=trace)
    attempt = next(e for e in trace.events if e["event"] == "attempt")
    assert len(attempt["raw_items"]) == 3 and len(attempt["candidates"]) == 1
    assert [r["reason"] for r in attempt["rejected"]] == ["no_valid_evidence", "invalid_or_unrequested_type"]
    warm = StageTrace()
    assert extract_candidates(NoCalls(), acts, [], single_chunk=True, cache=cached, trace=warm) == first
    assert next(e for e in warm.events if e["event"] == "part")["source"] == "cache"
    assert not any(e["event"] == "attempt" for e in warm.events)


class FixtureClient:
    """One explicit action in the first development meeting, with no follow-up needed."""

    def __init__(self):
        self.messages = NS(create=self.create)

    def create(self, **kwargs):
        name = kwargs["tool_choice"]["name"]
        if name == "submit_candidates":
            action_pass = "List ONLY actions" in kwargs["messages"][0]["content"]
            items = [{"type": "actions", "text": "UI will develop the menu", "evidence": [0],
                      "owner_ids": ["MTD023UID"], "owner_basis": "explicit", "deadline": None,
                      "deadline_basis": "missing", "status": "open"}] if action_pass else []
            payload = {"items": items}
        elif name == "submit_abstracts":
            payload = {"abstracts": [{"text": "Menu work was assigned.", "items": [0]}]}
        else:
            raise AssertionError(name)
        return NS(content=[NS(type="tool_use", name=name, input=payload)],
                  usage=NS(input_tokens=10, output_tokens=5))


def fixture_llm():
    return LLM(FixtureClient(), model="offline-test-fixture")


def test_run_metadata_and_cached_stages_preserve_record_schema(tmp_path):
    llm = fixture_llm()
    path = config.TRANSCRIPTS / "TS3006a.txt"
    first = run_meeting(path, "s1", llm, tmp_path, tmp_path / "cache")
    record = Path(first["output"]).read_bytes()
    meta = json.loads(Path(first["run_metadata"]).read_text(encoding="utf-8"))
    assert meta["status"] == "COMPLETED" and meta["model"]["name"] == llm.model
    assert meta["input"]["sha256"] == sha256(path)
    assert meta["code"]["git_commit"] and meta["code"]["source_digest"]
    assert meta["config"]["ACTION_SAMPLES"] == config.ACTION_SAMPLES
    assert (tmp_path / "s1" / "TS3006a.trace.jsonl").read_text(encoding="utf-8") == ""
    second = run_meeting(path, "s1", llm, tmp_path, tmp_path / "cache")
    assert Path(second["output"]).read_bytes() == record
    events = [json.loads(line) for line in Path(second["stage_trace"]).read_text(encoding="utf-8").splitlines()]
    assert next(e for e in events if e["event"] == "part")["source"] == "cache"
    assert next(e for e in events if e["stage"] == "assemble")["item_record_map"] == {"TS3006a#0": "TS3006a.sys.s.1"}
    assert first["llm calls"] > second["llm calls"]


def test_metadata_does_not_dump_secrets_or_endpoint_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value-never-save")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://private:password@example.invalid/v1?token=secret-query")
    llm = fixture_llm()
    llm.supports_effort = True
    identity = runtime_identity(llm)
    assert identity["model"]["endpoint"] == "https://example.invalid/v1"
    serialized = json.dumps(identity)
    assert all(s not in serialized for s in ("secret-value-never-save", "password", "secret-query"))


def test_blocked_input_still_saves_provenance(tmp_path):
    source = tmp_path / "TS3006a.txt"
    source.write_text("malformed transcript", encoding="utf-8")
    result = run_meeting(source, "s1", fixture_llm(), tmp_path / "output")
    assert result["status"] == "BLOCKED"
    meta = json.loads(Path(result["run_metadata"]).read_text(encoding="utf-8"))
    assert meta["status"] == "BLOCKED" and meta["usage"]["calls"] == 0


def test_baseline_guards_and_immutable_source_capture(tmp_path):
    with pytest.raises(ValueError, match="SB"):
        baseline.check_selection(["ES2004a"], ["s1"])
    with pytest.raises(ValueError, match="earlier"):
        baseline.check_selection(["TS3006b"], ["s2"])
    with pytest.raises(ValueError, match="source"):
        baseline.check_destination(baseline.REPOSITORY / "revised_gold_output" / "bad")
    destination = tmp_path / "baseline"
    manifest = baseline.capture(destination, ["TS3006a"], ["s1"], fixture_llm())
    assert manifest["status"] == "COMPLETED"
    assert manifest["execution_kind"] == "injected_client_test_fixture"
    assert manifest["artifact_sha256"]
    assert all(sha256(destination / name) == value for name, value in manifest["artifact_sha256"].items())
    assert (destination / "source" / "transcript_parser.py").is_file()
    assert not any(p.name == ".env" or "revised_gold_output" in p.parts for p in destination.rglob("*"))
    with pytest.raises(FileExistsError):
        baseline.capture(destination, ["TS3006a"], ["s1"], fixture_llm())


def test_baseline_failure_keeps_partial_manifest(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive-remote-message")
    monkeypatch.setattr(baseline, "run_meeting", fail)
    destination = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        baseline.capture(destination, ["TS3006a"], ["s1"], fixture_llm())
    manifest = json.loads((destination / "baseline.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ERROR" and manifest["error_type"] == "RuntimeError"
    assert "sensitive-remote-message" not in json.dumps(manifest)
