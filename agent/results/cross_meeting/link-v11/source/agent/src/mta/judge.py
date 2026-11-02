"""LLM judge for evaluation. Default: the pipeline model (`mta.llm.LLM`). Optional: Claude (`MTA_JUDGE=claude`).

`ClaudeJudge.forced_tool(system, user, name, schema)` has the same signature and return value as `LLM.forced_tool`,
so `mta.evaluate`, `mta.support` and `scripts/full_eval.py` take either. Claude Opus 5.5 does not accept a forced
`tool_choice`, so the answer is requested as JSON structured output (`output_config.format`) instead of a tool call.
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from . import config

FALLBACK_BETA = "server-side-fallback-2026-07-01"  # beta header for `fallbacks: "default"`


def get_judge():
    """The evaluation judge chosen by config.JUDGE."""
    if config.JUDGE == "claude":
        return ClaudeJudge()
    from .llm import LLM
    return LLM()


def cache_path(folder: Path, base: str, judge) -> Path:
    """Judge caches are keyed by text only, so each non-default judge gets its own file. The pipeline-model judge
    keeps the original names (and the judgements already stored there)."""
    tag = getattr(judge, "tag", None)
    return folder / (f"{base}_{tag}.json" if tag else f"{base}.json")


def shared_cache(base: str, judge) -> "JsonCache":
    """One cache per judgement kind for ALL runs and modes (runs/_judge/). Keys are hashes of exactly what the judge
    sees, so identical text (s1 vs s2, or a rerun that did not change an item list) always gets the same judgement."""
    return JsonCache.get(cache_path(config.RUNS / "_judge", base, judge))


class JsonCache:
    """A dict persisted to one JSON file, shared by every thread of the process (one instance per path)."""
    _instances: dict[Path, "JsonCache"] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def get(cls, path: Path) -> "JsonCache":
        path = Path(path).resolve()
        with cls._instances_lock:
            if path not in cls._instances:
                cls._instances[path] = cls(path)
            return cls._instances[path]

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def lock_for(self, key: str) -> threading.Lock:
        """Held while a judgement for `key` is being made, so two threads asking for the same thing (e.g. s1 and s2
        with identical items) wait for one judgement instead of each making, and using, their own."""
        with self._lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def compute(self, key: str, make):
        """The cached value for `key`, or make() it once (other threads asking for the same key wait)."""
        if key in self:
            return self[key]
        with self.lock_for(key):
            if key in self:
                return self[key]
            value = make()
            self[key] = value
            return value

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def __getitem__(self, key: str):
        with self._lock:
            return self._data[key]

    def get_value(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def __setitem__(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data), encoding="utf-8")
            tmp.replace(self.path)  # never leave a half-written cache behind

    def update_from(self, other: dict) -> None:
        """Merge judgements from an older per-run cache file (existing keys win)."""
        with self._lock:
            for k, v in other.items():
                self._data.setdefault(k, v)
        if other:
            k0 = next(iter(other))
            self[k0] = self.get_value(k0)  # write once


def strict_schema(schema: dict) -> dict:
    """Structured outputs need closed objects: every object gets additionalProperties=false and all of its properties
    required. Numeric bounds are dropped; the prompts state the scales."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
                for sub in node["properties"].values():
                    walk(sub)
            for key in ("minimum", "maximum"):
                if isinstance(node.get(key), (int, float)):
                    node.pop(key)
            if "items" in node:
                walk(node["items"])
        elif isinstance(node, list):
            for sub in node:
                walk(sub)

    walk(s)
    return s


class ClaudeJudge:
    def __init__(self, client=None, model: str | None = None, effort: str | None = None):
        if client is None:
            import anthropic  # only needed for evaluation runs
            client = anthropic.Anthropic(max_retries=4)
        self.client = client
        self.model = model or config.JUDGE_MODEL
        self.effort = effort or config.JUDGE_EFFORT
        self.in_tokens = self.out_tokens = self.calls = 0
        self._lock = threading.Lock()

    @property
    def tag(self) -> str:
        """Used in cache file names, so judgements from different judge models never mix."""
        return self.model.replace("/", "_").replace("@", "_")

    def forced_tool(self, system: str, user: str, name: str, schema: dict, max_tokens: int = 32000,
                    effort: str | None = None) -> dict:
        request = dict(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort or self.effort,
                           "format": {"type": "json_schema", "schema": strict_schema(schema)}},
            # If a safety classifier declines, the API re-runs the request on its recommended fallback model.
            betas=[FALLBACK_BETA], fallbacks="default",
        )
        problem = None
        for _ in range(2):
            # Streaming: transcripts are long and max_tokens is large, which can exceed non-streaming HTTP timeouts.
            with self.client.beta.messages.stream(**request) as stream:
                resp = stream.get_final_message()
            u = getattr(resp, "usage", None)
            with self._lock:
                self.calls += 1
                if u:
                    self.in_tokens += getattr(u, "input_tokens", 0) or 0
                    self.out_tokens += getattr(u, "output_tokens", 0) or 0
            if resp.stop_reason == "refusal":
                details = getattr(resp, "stop_details", None)
                raise RuntimeError(f"{name}: {self.model} refused ({getattr(details, 'category', None)})")
            if resp.stop_reason == "max_tokens":
                problem = "hit max_tokens"
                continue
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                problem = f"invalid JSON: {e}"
        raise RuntimeError(f"{name}: no usable answer from {self.model} after 2 attempts ({problem})")
