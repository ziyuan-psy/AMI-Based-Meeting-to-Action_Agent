"""Shared-budget races, real timeout bounds and partial artifacts. All transports are offline fakes."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mta import config, observation, pipeline  # noqa: E402
from mta.control import Limits, RunControl, RunStopped, stage_pool  # noqa: E402
from mta.llm import LLM, OpenAIAdapter  # noqa: E402
from mta.trace import StageTrace  # noqa: E402
from test_observation import FixtureClient, fixture_llm  # noqa: E402


def limits(**changes):
    values = dict(calls=80, tokens=1000000, request_seconds=1, meeting_seconds=5, inflight=2, retries=0, backoff=0)
    return Limits(**{**values, **changes})


class Client:
    def __init__(self, handler=None):
        self.handler = handler or (lambda kwargs: NS(content=[], usage=NS(input_tokens=8, output_tokens=2)))
        self.calls = self.active = self.peak = 0
        self._lock = threading.Lock()
        self.messages = NS(create=self.create)

    def create(self, **kwargs):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            return self.handler(kwargs)
        finally:
            with self._lock:
                self.active -= 1


def request(llm):
    return llm.create("system", [{"role": "user", "content": "input"}], max_tokens=16, stage="extract")


def test_parallel_attempts_cannot_overspend_calls_or_inflight_slots():
    def slow(kwargs):
        time.sleep(0.02)
        return NS(content=[], usage=NS(input_tokens=8, output_tokens=2))
    client, control = Client(slow), RunControl(limits(calls=3))
    scoped = LLM(client).scoped(control)
    def one(_):
        try:
            request(scoped)
            return True
        except RunStopped:
            return False
    with ThreadPoolExecutor(12) as pool:
        outcomes = list(pool.map(one, range(20)))
    control.seal()
    assert client.calls == 3 and sum(outcomes) == 3
    assert client.peak <= 2 and control.snapshot()["max_inflight_seen"] <= 2
    assert control.snapshot()["charged_tokens"] == 30


def test_parallel_token_reservations_wait_for_settlement():
    control = RunControl(limits(tokens=100))
    first = control.reserve("extract", 60, 40, 20)
    with ThreadPoolExecutor(1) as pool:
        second_job = pool.submit(control.reserve, "abstract", 60, 40, 20)
        time.sleep(0.02)
        assert not second_job.done()
        control.settle(first, NS(input_tokens=20, output_tokens=10))
        second = second_job.result(timeout=1)
        assert control.snapshot()["charged_tokens"] + control.snapshot()["reserved_tokens"] == 90
        control.settle(second, NS(input_tokens=20, output_tokens=30))
    with pytest.raises(RunStopped, match="token_budget"):
        control.reserve("agent", 60, 40, 20)
    assert control.snapshot()["charged_tokens"] == 80


def test_missing_usage_and_provider_overrun_are_not_treated_as_free():
    client = Client(lambda kwargs: NS(content=[], usage=None))
    control = RunControl(limits())
    request(LLM(client).scoped(control))
    stats = control.snapshot()
    assert stats["charged_tokens"] == stats["attempts"][0]["reserved_tokens"]
    assert stats["unknown_usage_tokens"] == stats["charged_tokens"]
    control2 = RunControl(limits())
    attempt = control2.reserve("extract", 10, 5, 5)
    with pytest.raises(RunStopped, match="token_accounting_overrun"):
        control2.settle(attempt, NS(input_tokens=10, output_tokens=1))


class HTTPError(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__("sensitive-provider-message")


@pytest.mark.parametrize("max_calls,expected_calls,success", [(2, 2, False), (5, 3, True)])
def test_transport_retries_use_the_same_call_and_token_budget(max_calls, expected_calls, success):
    answers = [HTTPError(429), HTTPError(503), NS(content=[], usage=NS(input_tokens=8, output_tokens=2))]
    def handler(kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    client, control = Client(handler), RunControl(limits(calls=max_calls, retries=2))
    scoped = LLM(client).scoped(control)
    if success:
        request(scoped)
    else:
        with pytest.raises(RunStopped, match="call_budget"):
            request(scoped)
    control.seal()
    assert client.calls == expected_calls == control.snapshot()["attempted_calls"]
    assert control.snapshot()["unknown_usage_tokens"] > 0


def test_authentication_error_is_not_retried_and_message_is_not_saved():
    trace = StageTrace()
    control = RunControl(limits(retries=2), trace)
    def fail(kwargs):
        raise HTTPError(401)
    client = Client(fail)
    with pytest.raises(RunStopped) as error:
        request(LLM(client).scoped(control))
    assert client.calls == 1 and error.value.failure["http_status"] == 401
    assert "sensitive-provider-message" not in json.dumps(trace.events)


def test_structured_output_retries_cannot_escape_global_budget():
    client, control = Client(), RunControl(limits(calls=2))
    with pytest.raises(RunStopped, match="call_budget"):
        LLM(client).scoped(control).forced_tool("s", "u", "submit", {"type": "object"})
    assert client.calls == 2


def test_request_timeout_returns_without_waiting_for_uncooperative_transport():
    release = threading.Event()
    def hang(kwargs):
        release.wait(2)
        return NS(content=[], usage=NS(input_tokens=8, output_tokens=2))
    control, client = RunControl(limits(request_seconds=0.03, retries=2)), Client(hang)
    started = time.monotonic()
    try:
        with pytest.raises(RunStopped, match="request_timeout"):
            request(LLM(client).scoped(control))
        assert time.monotonic() - started < 0.5
        control.seal()
        assert client.calls == 1 and control.snapshot()["unknown_usage_tokens"] > 0
    finally:
        release.set()


def test_openai_adapter_disables_hidden_sdk_retries_and_sets_timeout():
    class SDK:
        def __init__(self):
            self.options = []
            self.chat = NS(completions=NS(create=self.create))
        def with_options(self, **options):
            self.options.append(options)
            return self
        def create(self, **kwargs):
            assert kwargs["max_completion_tokens"] == config.OPENAI_MIN_COMPLETION_TOKENS
            return NS(choices=[NS(message=NS(content="ok", tool_calls=[]))],
                      usage=NS(prompt_tokens=8, completion_tokens=2))
    adapter = OpenAIAdapter.__new__(OpenAIAdapter)
    adapter.client = SDK()
    adapter.messages = NS(create=adapter.create)
    control = RunControl(limits(request_seconds=0.5))
    request(LLM(adapter).scoped(control))
    assert adapter.client.options[0]["max_retries"] == 0
    assert 0 < adapter.client.options[0]["timeout"] <= 0.5


def test_other_sdk_clients_receive_controlled_options():
    class SDK(Client):
        def with_options(self, **options):
            self.options = options
            return self
    client = SDK()
    request(LLM(client).scoped(RunControl(limits())))
    assert client.options["max_retries"] == 0 and client.options["timeout"] <= 1


def test_meeting_deadline_covers_local_stages_and_prevents_late_writes(tmp_path, monkeypatch):
    original = pipeline.parse_transcript_file
    release = threading.Event()
    def hang(*args, **kwargs):
        release.wait(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(pipeline, "parse_transcript_file", hang)
    monkeypatch.setattr(config, "MEETING_TIMEOUT", 0.04)
    try:
        result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path)
        assert result["status"] == "TIMED_OUT" and result["failed_stage"] == "parse"
        assert result["runtime_control"]["attempted_calls"] == 0
        files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        release.set()
        time.sleep(0.08)
        assert files == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        assert Path(result["partial_artifact"]).is_file()
    finally:
        release.set()


@pytest.mark.parametrize("setting,value,code", [("MAX_MODEL_CALLS", 0, "call_budget_exhausted"),
                                                  ("MAX_TOTAL_TOKENS", 1, "token_budget_exhausted")])
def test_meeting_budget_failure_saves_checkpoints_without_publishing(tmp_path, monkeypatch, setting, value, code):
    monkeypatch.setattr(config, setting, value)
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "BUDGET_EXCEEDED" and result["reason"] == code
    assert result["runtime_control"]["attempted_calls"] == 0
    meta = json.loads(Path(result["run_metadata"]).read_text(encoding="utf-8"))
    assert Path(meta["checkpoints"]["parsed"]).is_file()
    assert Path(result["partial_artifact"]).is_file()
    assert not (tmp_path / "s1/TS3006a.json").exists()


@pytest.mark.parametrize("function,stage", [("parse_transcript_file", "parse"), ("Store", "participants"),
    ("extract_candidates", "extract"), ("consolidate", "consolidate"), ("select_items", "select"), ("normalize", "normalize"),
    ("flag_items", "triage"), ("_warm_store", "context"), ("make_record", "assemble"),
    ("check", "validate"), ("render_report", "render")])
def test_each_stage_exits_with_a_named_failure_and_preserved_artifacts(tmp_path, monkeypatch, function, stage):
    def fail(*args, **kwargs):
        raise OSError("private-failure-details")
    monkeypatch.setattr(pipeline, function, fail)
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    expected_status = "BLOCKED" if stage == "parse" else "FAILED"
    assert result["status"] == expected_status and result["failed_stage"] == stage
    assert not (tmp_path / "s1/TS3006a.json").exists()
    assert Path(result["partial_artifact"]).exists()
    meta = json.loads(Path(result["run_metadata"]).read_text(encoding="utf-8"))
    assert meta["status"] == expected_status and "private-failure-details" not in json.dumps(meta)
    assert all(Path(p).is_file() for p in meta["checkpoints"].values())


def test_publish_failure_restores_previous_valid_record_and_report(tmp_path, monkeypatch):
    path = config.TRANSCRIPTS / "TS3006a.txt"
    first = pipeline.run_meeting(path, "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    record, report = Path(first["output"]), tmp_path / "s1/TS3006a_report.md"
    before = (record.read_bytes(), report.read_bytes())
    assert pipeline.saved_run_complete(record)
    original = observation.write_text
    def fail_report(path, text, control=None):
        if path == report:
            raise OSError("cannot publish report")
        return original(path, text, control)
    monkeypatch.setattr(observation, "write_text", fail_report)
    result = pipeline.run_meeting(path, "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "FAILED" and result["failed_stage"] == "publish"
    assert (record.read_bytes(), report.read_bytes()) == before
    assert Path(result["partial_artifact"]).is_file()
    assert not pipeline.saved_run_complete(record)


def test_request_timeout_seals_all_artifacts_and_does_not_retry(tmp_path, monkeypatch):
    release = threading.Event()
    original = FixtureClient.create
    class Slow(FixtureClient):
        def create(self, **kwargs):
            release.wait(2)
            return original(self, **kwargs)
    monkeypatch.setattr(config, "REQUEST_TIMEOUT", 0.03)
    monkeypatch.setattr(config, "MAX_INFLIGHT", 2)
    llm = LLM(Slow(), model="offline")
    try:
        result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", llm, tmp_path, tmp_path / "cache")
        assert result["status"] == "TIMED_OUT" and result["reason"] == "request_timeout"
        assert result["runtime_control"]["attempted_calls"] <= 2
        assert result["runtime_control"]["unknown_usage_tokens"] > 0
        files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        release.set()
        time.sleep(0.08)
        assert files == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        assert llm.calls == 0
    finally:
        release.set()


def test_retry_backoff_is_interrupted_by_the_meeting_deadline():
    def fail(kwargs):
        raise HTTPError(429)
    client = Client(fail)
    control = RunControl(limits(meeting_seconds=0.04, retries=5, backoff=1))
    with pytest.raises(RunStopped, match="meeting_timeout"):
        request(LLM(client).scoped(control))
    assert client.calls == 1


def test_usage_is_isolated_when_meetings_share_one_client():
    parent = LLM(Client())
    one, two = parent.scoped(RunControl(limits())), parent.scoped(RunControl(limits()))
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(request, scoped) for scoped in (one, two)]
        for job in jobs:
            job.result()
    assert one.usage_snapshot()["calls"] == two.usage_snapshot()["calls"] == 1
    assert parent.usage_snapshot()["calls"] == 2


def test_validation_failure_keeps_draft_and_stops_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "check", lambda *args: ["invalid record"])
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "BLOCKED" and result["reason"] == "validation_failed"
    assert not (tmp_path / "s1/TS3006a.json").exists()
    assert (tmp_path / "s1/TS3006a.blocked.json").exists()
    meta = json.loads(Path(result["run_metadata"]).read_text(encoding="utf-8"))
    assert Path(meta["checkpoints"]["assembled"]).is_file()
    assert Path(meta["checkpoints"]["validated"]).is_file()


def test_initialization_and_finalization_failures_do_not_report_success(tmp_path, monkeypatch):
    original = pipeline.RunObservation
    def fail(*args, **kwargs):
        raise OSError("cannot write metadata")
    monkeypatch.setattr(pipeline, "RunObservation", fail)
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path)
    assert result["status"] == "FAILED" and result["failed_stage"] == "initialize"
    assert result["artifacts_saved"] is False
    monkeypatch.setattr(pipeline, "RunObservation", original)
    monkeypatch.setattr(original, "finish", fail)
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "FAILED" and result["failed_stage"] == "finalize"
    assert not pipeline.saved_run_complete(Path(result["output"]))


def test_cancellation_preserves_checkpoints(tmp_path, monkeypatch):
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(pipeline, "normalize", cancel)
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "CANCELLED" and Path(result["partial_artifact"]).is_file()


def test_trace_archive_failure_cannot_leave_a_completed_commit_marker(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("trace archive unavailable")
    monkeypatch.setattr(observation.shutil, "copyfile", fail)
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "FAILED" and result["failed_stage"] == "finalize"
    assert result["artifacts_saved"] is False
    assert not pipeline.saved_run_complete(Path(result["output"]))
    assert json.loads(Path(result["run_metadata"]).read_text(encoding="utf-8"))["status"] == "FAILED"


def test_cli_and_series_runner_stop_after_first_failed_meeting(tmp_path, monkeypatch):
    from mta import run
    calls = []
    def failure(path, *args, **kwargs):
        calls.append(path.stem)
        return {"status": "BUDGET_EXCEEDED", "reason": "call_budget_exhausted"}
    monkeypatch.setattr(run, "LLM", fixture_llm)
    monkeypatch.setattr(run, "run_meeting", failure)
    assert run.main(["TS3006", "--out", str(tmp_path)]) == 1
    assert calls == ["TS3006a"]
    script = config.ROOT / "scripts/run_series.py"
    spec = importlib.util.spec_from_file_location("controlled_series_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LLM", fixture_llm)
    monkeypatch.setattr(module, "run_meeting", failure)
    calls.clear()
    assert module.main(["TS3006", "s2", str(tmp_path)]) == 1
    assert calls == ["TS3006a"]


def test_sealing_cancels_queued_work_and_shuts_down_stage_workers():
    started, release = threading.Event(), threading.Event()
    control = RunControl(limits())
    pool = stage_pool(1, control)
    def hang():
        started.set()
        release.wait(2)
    first = pool.submit(hang)
    assert started.wait(1)
    pending = pool.submit(lambda: pytest.fail("queued work ran after stop"))
    control.stop("meeting_timeout", "TIMED_OUT")
    control.seal()
    assert pending.cancelled()
    release.set()
    first.result(timeout=1)
    for worker in pool.workers:
        worker.join(timeout=1)
        assert not worker.is_alive()


def test_parallel_local_failure_interrupts_an_earlier_hung_job():
    release, started = threading.Event(), threading.Event()
    control = RunControl(limits(meeting_seconds=2))
    control.enter("extract")
    def hang():
        started.set()
        release.wait(2)
    def fail():
        assert started.wait(1)
        raise OSError("private-worker-error")
    try:
        with stage_pool(2, control) as pool:
            first = pool.submit(hang)
            pool.submit(fail)
            beginning = time.monotonic()
            with pytest.raises(RunStopped, match="stage_failed"):
                control.wait(first)
            assert time.monotonic() - beginning < 0.5
            assert control.snapshot()["failure"]["stage"] == "extract"
    finally:
        control.seal()
        release.set()


def test_agent_local_exception_is_failed_instead_of_silent_fallback(tmp_path, monkeypatch):
    def flag(items, store):
        for item in items:
            item["flags"] = ["OWNER_CHECK"]
    def fail(*args, **kwargs):
        raise OSError("private-agent-error")
    monkeypatch.setattr(pipeline, "flag_items", flag)
    monkeypatch.setattr(pipeline, "run_agent", fail)
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert result["status"] == "FAILED" and result["failed_stage"] == "agent+abstract"
    assert Path(result["partial_artifact"]).is_file()
    assert not (tmp_path / "s1/TS3006a.json").exists()
    assert "private-agent-error" not in Path(result["stage_trace"]).read_text(encoding="utf-8")


def test_meeting_timeout_wins_over_longer_request_timeout():
    release, started = threading.Event(), threading.Event()
    def transport(kwargs):
        started.set()
        release.wait(2)
    client = Client(transport)
    control = RunControl(limits(meeting_seconds=2, request_seconds=3))
    try:
        with ThreadPoolExecutor(1) as pool:
            job = pool.submit(request, LLM(client).scoped(control))
            assert started.wait(1)  # test a request already in flight, independent of thread startup latency
            control.deadline = time.monotonic() + 0.03
            with pytest.raises(RunStopped, match="meeting_timeout"):
                job.result(timeout=1)
        assert client.calls == 1
    finally:
        control.seal()
        release.set()


def test_failed_attempt_trace_references_survive_a_later_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_MODEL_CALLS", 0)
    first = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    partial = json.loads(Path(first["partial_artifact"]).read_text(encoding="utf-8"))
    old_trace = Path(partial["stages"]).read_bytes()
    monkeypatch.setattr(config, "MAX_MODEL_CALLS", 80)
    second = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", fixture_llm(), tmp_path, tmp_path / "cache")
    assert second["status"] == "COMPLETED"
    assert Path(partial["stages"]).read_bytes() == old_trace
    assert Path(partial["agent_trace"]).is_file()


@pytest.mark.parametrize("setting,value", [("MAX_MODEL_CALLS", -1), ("MAX_TOTAL_TOKENS", -1),
    ("MAX_INFLIGHT", 0), ("TRANSPORT_RETRIES", -1), ("REQUEST_TIMEOUT", 0),
    ("MEETING_TIMEOUT", float("inf")), ("RETRY_BACKOFF", float("nan"))])
def test_invalid_limits_fail_before_calls_or_invalid_metadata(tmp_path, monkeypatch, setting, value):
    monkeypatch.setattr(config, setting, value)
    llm = fixture_llm()
    result = pipeline.run_meeting(config.TRANSCRIPTS / "TS3006a.txt", "s1", llm, tmp_path)
    assert result["status"] == "FAILED" and result["reason"] == "invalid_limits"
    assert result["failed_stage"] == "configuration" and llm.calls == 0
    assert not list(tmp_path.rglob("*.json"))
