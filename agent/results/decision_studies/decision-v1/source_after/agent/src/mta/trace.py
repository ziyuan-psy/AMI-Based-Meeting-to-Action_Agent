"""Decision log: one JSON line per agent step."""
from __future__ import annotations

import json
import copy
import threading
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.events: list[dict] = []
        self._lock = threading.Lock()  # agent items log from parallel threads
        self._closed = False
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def log(self, **event) -> None:
        event["t"] = round(time.time(), 2)
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self._lock:
            if self._closed:
                return
            self.events.append(event)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)

    def close(self):
        with self._lock:
            self._closed = True


class StageTrace:
    """Independent stage snapshots; the existing agent decision-log format stays unchanged."""

    def __init__(self, path: Path | None = None, **context):
        self.path = path
        self.context = context
        self.events: list[dict] = []
        self._lock = threading.Lock()
        self._closed = False
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def log(self, **event) -> None:
        # Later stages may mutate the same dictionaries. Keep their earlier state.
        snapshot = copy.deepcopy({**self.context, **event})
        with self._lock:
            if self._closed:
                return
            snapshot.update(seq=len(self.events), t=round(time.time(), 3))
            self.events.append(snapshot)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    def finish(self, **event):
        """One terminal event, then late workers can no longer append."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            snapshot = copy.deepcopy({**self.context, **event, "seq": len(self.events), "t": round(time.time(), 3)})
            self.events.append(snapshot)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
