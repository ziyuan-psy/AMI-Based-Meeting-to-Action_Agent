"""Meeting-wide admission, cancellation and daemon workers. No provider credentials are stored."""
from __future__ import annotations

import copy
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import math
from queue import Empty, Queue
import threading
import time

from . import config


@dataclass(frozen=True)
class Limits:
    calls: int
    tokens: int
    request_seconds: float
    meeting_seconds: float
    inflight: int
    retries: int
    backoff: float

    @classmethod
    def configured(cls):
        limits = cls(config.MAX_MODEL_CALLS, config.MAX_TOTAL_TOKENS, config.REQUEST_TIMEOUT,
                     config.MEETING_TIMEOUT, config.MAX_INFLIGHT, config.TRANSPORT_RETRIES, config.RETRY_BACKOFF)
        if (limits.calls < 0 or limits.tokens < 0 or limits.inflight < 1 or limits.retries < 0
                or not all(math.isfinite(x) and x > 0 for x in (limits.request_seconds, limits.meeting_seconds))
                or not math.isfinite(limits.backoff) or limits.backoff < 0):
            raise ValueError("Invalid whole-meeting resource limits")
        return limits


class RunStopped(Exception):
    def __init__(self, failure: dict):
        self.failure = failure
        super().__init__(failure["code"])


def launch(function) -> Future:
    """A timed-out transport/local stage cannot hold Python's process shutdown hostage."""
    future = Future()
    def work():
        try:
            future.set_result(function())
        except BaseException as error:
            future.set_exception(error)
    threading.Thread(target=work, daemon=True, name="mta-bounded-work").start()
    return future


def usage_tokens(usage):
    if usage is None:
        return None
    values = [getattr(usage, name, None) for name in ("input_tokens", "output_tokens")]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    cached = [getattr(usage, name, 0) or 0 for name in ("cache_read_input_tokens", "cache_creation_input_tokens")]
    if any(type(value) is not int or value < 0 for value in cached):
        return None
    return values[0] + sum(cached), values[1]


class RunControl:
    def __init__(self, limits: Limits | None = None, trace=None):
        self.limits = limits or Limits.configured()
        self.trace = trace
        self.started = time.monotonic()
        self.deadline = self.started + self.limits.meeting_seconds
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._cancelled = threading.Event()
        self._sealed = False
        self.failure = None
        self.stage = "parse"
        self.attempts = []
        self._active = {}
        self._pools = []
        self.charged_tokens = 0
        self.unknown_tokens = 0
        self.max_inflight_seen = 0

    def _event(self, event, **data):
        if self.trace is not None:
            if "stage" in data:
                data["request_stage"] = data.pop("stage")
            self.trace.log(stage="control", event=event, **data)

    def stop(self, code: str, status: str, stage: str | None = None, **details):
        with self._condition:
            if self.failure is None:
                self.failure = {"code": code, "status": status, "stage": stage or self.stage, **details}
                first = True
            else:
                first = False
            self._cancelled.set()
            self._condition.notify_all()
            if first:
                self._event("stopped", **self.failure)
            return RunStopped(dict(self.failure))

    def check(self, stage: str | None = None):
        with self._lock:
            if self.failure is not None:
                raise RunStopped(dict(self.failure))
            if self._sealed:
                raise RunStopped({"code": "run_closed", "status": "FAILED", "stage": stage or self.stage})
            if time.monotonic() >= self.deadline:
                raise self.stop("meeting_timeout", "TIMED_OUT", stage)

    @contextmanager
    def guard(self):
        """Fence checkpoint/cache/publication writes against finalization and late workers."""
        with self._lock:
            self.check()
            yield

    def enter(self, stage):
        with self.guard():
            self.stage = stage
            self._event("stage_started", current_stage=stage)

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())

    def reserve(self, stage: str, tokens: int, input_bound: int, output_cap: int):
        with self._condition:
            while True:
                self.check(stage)
                if tokens > self.limits.tokens:
                    raise self.stop("token_budget_exhausted", "BUDGET_EXCEEDED", stage,
                                    requested_reservation=tokens)
                available = self.limits.tokens - self.charged_tokens - sum(self._active.values())
                exhausted = len(self.attempts) >= self.limits.calls
                if (exhausted or available < tokens) and not self._active:
                    code = "call_budget_exhausted" if exhausted else "token_budget_exhausted"
                    raise self.stop(code, "BUDGET_EXCEEDED", stage)
                if not exhausted and available >= tokens and len(self._active) < self.limits.inflight:
                    attempt = len(self.attempts)
                    self.attempts.append({"attempt": attempt, "stage": stage, "reserved_tokens": tokens,
                                          "input_bound": input_bound, "output_cap": output_cap,
                                          "outcome": "in_flight"})
                    self._active[attempt] = tokens
                    self.max_inflight_seen = max(self.max_inflight_seen, len(self._active))
                    self._event("request_admitted", **self.attempts[-1])
                    return attempt
                self._condition.wait(min(0.05, self.remaining()))

    def settle(self, attempt, usage=None, error_type=None, response_tools=None):
        with self._condition:
            self.check(self.attempts[attempt]["stage"])
            reserved = self._active.pop(attempt)
            measured = usage_tokens(usage)
            known = measured is not None
            tokens = sum(measured) if known else reserved
            self.charged_tokens += tokens
            if not known:
                self.unknown_tokens += tokens
            entry = self.attempts[attempt]
            entry.update(outcome="error" if error_type else "completed", charged_tokens=tokens,
                         usage_known=known, error_type=error_type)
            if known:
                entry.update(input_tokens=measured[0], output_tokens=measured[1])
            self._event("request_finished", **entry, response_tools=response_tools or [])
            self._condition.notify_all()
            if tokens > reserved:
                raise self.stop("token_accounting_overrun", "BUDGET_EXCEEDED", entry["stage"])

    def wait(self, future: Future, stage=None, request_timeout=None):
        request_deadline = time.monotonic() + request_timeout if request_timeout is not None else None
        while True:
            self.check(stage)
            if future.done():
                result = future.result()
                self.check(stage)
                return result
            if request_deadline is not None and time.monotonic() >= request_deadline:
                raise self.stop("request_timeout", "TIMED_OUT", stage)
            delay = min(0.05, self.remaining())
            if request_deadline is not None:
                delay = min(delay, max(0, request_deadline - time.monotonic()))
            try:
                result = future.result(timeout=delay)
                self.check(stage)
                return result
            except FutureTimeout:
                pass

    def pause(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check()
            self._cancelled.wait(min(0.05, until - time.monotonic()))
        self.check()

    def seal(self):
        with self._condition:
            self._sealed = True
            for attempt, reserved in self._active.items():
                self.charged_tokens += reserved
                self.unknown_tokens += reserved
                self.attempts[attempt].update(outcome="abandoned", charged_tokens=reserved, usage_known=False)
            self._active.clear()
            for pool in self._pools:
                pool.close()
            self._cancelled.set()
            self._condition.notify_all()

    def snapshot(self):
        with self._lock:
            return copy.deepcopy({"limits": asdict(self.limits), "attempted_calls": len(self.attempts),
                                  "charged_tokens": self.charged_tokens, "unknown_usage_tokens": self.unknown_tokens,
                                  "reserved_tokens": sum(self._active.values()), "max_inflight_seen": self.max_inflight_seen,
                                  "failure": self.failure, "attempts": self.attempts,
                                  "input_bound_method": "utf8_request_bytes_plus_padding",
                                  "unknown_usage_policy": "charge_full_reservation"})

    def usage_snapshot(self):
        """Per-meeting measured usage, independent of another meeting sharing the same client."""
        with self._lock:
            result = {"calls": 0, "in": 0, "out": 0, "stages": {}}
            for attempt in self.attempts:
                if attempt["outcome"] != "completed":
                    continue
                values = {"calls": 1, "in": attempt.get("input_tokens", 0), "out": attempt.get("output_tokens", 0)}
                stage = result["stages"].setdefault(attempt["stage"], {"calls": 0, "in": 0, "out": 0})
                for key, value in values.items():
                    stage[key] += value
                    result[key] += value
            return result


def input_token_bound(system, messages, tools, tool_choice):
    payload = {"system": system, "messages": messages, "tools": tools, "tool_choice": tool_choice}
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + config.INPUT_TOKEN_PADDING


class DaemonPool:
    def __init__(self, workers, control):
        self.control, self.queue = control, Queue()
        self._closed = False
        self._close_lock = threading.Lock()
        self.workers = [threading.Thread(target=self._work, daemon=True, name="mta-stage-worker")
                        for _ in range(workers)]
        with control.guard():
            control._pools.append(self)
        for worker in self.workers:
            worker.start()

    def _work(self):
        while True:
            job = self.queue.get()
            if job is None:
                return
            future, function, args = job
            if not future.set_running_or_notify_cancel():
                continue
            try:
                self.control.check()
                future.set_result(function(*args))
            except BaseException as error:
                # Fail immediately even when the collector is waiting on a different, hung job.
                # Expected model/tool-policy fallbacks return values instead of raising here.
                if not isinstance(error, RunStopped):
                    try:
                        self.control.stop("cancelled" if isinstance(error, KeyboardInterrupt) else "stage_failed",
                                          "CANCELLED" if isinstance(error, KeyboardInterrupt) else "FAILED",
                                          error_type=type(error).__name__, os_error=getattr(error,'errno',None),
                                          windows_error=getattr(error,'winerror',None), io_operation=getattr(error,'mta_io_operation',None))
                    finally:
                        future.set_exception(error)
                else:
                    future.set_exception(error)

    def submit(self, function, *args):
        with self.control.guard():
            if self._closed:
                raise RuntimeError("Stage pool is closed")
            future = Future()
            self.queue.put((future, function, args))
            return future

    def map(self, function, values):
        futures = [self.submit(function, value) for value in values]
        return [self.control.wait(future) for future in futures]

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            while True:
                try:
                    pending = self.queue.get_nowait()
                except Empty:
                    break
                if pending is not None:
                    pending[0].cancel()
            for _ in self.workers:
                self.queue.put(None)


def stage_pool(workers: int, control=None):
    return DaemonPool(workers, control) if control is not None else ThreadPoolExecutor(max_workers=workers)
