"""Decision log: one JSON line per agent step."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.events: list[dict] = []
        self._lock = threading.Lock()  # agent items log from parallel threads
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def log(self, **event) -> None:
        event["t"] = round(time.time(), 2)
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self._lock:
            self.events.append(event)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
