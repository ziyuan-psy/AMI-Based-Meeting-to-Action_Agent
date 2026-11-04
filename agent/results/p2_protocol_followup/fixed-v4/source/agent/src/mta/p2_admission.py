"""Fair P2 attempt admission within the original meeting-wide call/token controller."""
from __future__ import annotations

import threading


class ItemAllowanceExhausted(Exception):
    pass


class P2Admission:
    def __init__(self, control, items, tail_calls=3):
        self.control = control
        self._lock = threading.Lock()
        self.pending = set(items)
        self.counts = {key:0 for key in items}
        self.initial_attempts = control.snapshot()['attempted_calls']
        self.capacity = max(0, control.limits.calls - self.initial_attempts - tail_calls)
        self.tail_calls = tail_calls

    def take(self, key):
        with self._lock:
            issued = sum(self.counts.values())
            first_waiting = sum(self.counts[k] == 0 for k in self.pending if k != key)
            allowed = issued < self.capacity and (self.counts[key] == 0 or self.capacity-issued > first_waiting)
            if allowed:
                self.counts[key] += 1
            return allowed

    def done(self, key):
        with self._lock:
            self.pending.discard(key)

    def view(self, key):
        return ItemControl(self, key)

    def snapshot(self):
        with self._lock:
            return {'capacity':self.capacity,'tail_calls_reserved':self.tail_calls,
                    'attempts_by_item':dict(self.counts),'issued':sum(self.counts.values())}


class ItemControl:
    def __init__(self, admission, key):
        self.admission, self.key = admission, key
        self.parent = admission.control
        self.last_transport_error = None

    def __getattr__(self, name):
        return getattr(self.parent, name)

    def reserve(self, stage, *args):
        self.parent.check(stage)
        if not self.admission.take(self.key):
            self.parent._event('p2_allowance_exhausted', item=self.key, **self.admission.snapshot())
            # An actual transport error must remain a run failure, never become a pending fact check.
            if self.last_transport_error:
                raise self.parent.stop('model_request_failed','FAILED',stage,error_type=self.last_transport_error,
                                       retries_exhausted=True, allowance_exhausted=True)
            raise ItemAllowanceExhausted('p2_call_allowance_exhausted')
        return self.parent.reserve(stage,*args)

    def settle(self, attempt, usage=None, error_type=None, response_tools=None):
        self.last_transport_error = error_type
        return self.parent.settle(attempt,usage=usage,error_type=error_type,response_tools=response_tools)
