"""Run provenance and stage artifacts. Only explicit, non-secret settings are recorded."""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from . import config
from .trace import StageTrace

REPOSITORY = Path(__file__).resolve().parents[3]
SETTINGS = (
    "CHUNK", "OVERLAP", "WORKERS", "EMPTY_RETRIES", "ACTION_SAMPLES", "CONSOLIDATE_SAMPLES", "MAX_ITEMS",
    "EFFORT_EXTRACT", "EFFORT_CONSOLIDATE", "EFFORT_AGENT", "EFFORT_ABSTRACT", "PRIOR_JACCARD",
    "MAX_CALLS_PER_ITEM", "MAX_CALLS_PER_MEETING", "MAX_INVALID_ACTIONS", "MAX_NO_PROGRESS",
    "MAX_FINISH_TRIES", "MAX_AGENT_STEPS", "OPENAI_MIN_COMPLETION_TOKENS",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def source_files() -> list[Path]:
    return sorted([REPOSITORY / "transcript_parser.py", *config.ROOT.glob("src/mta/*.py"),
                   *config.ROOT.glob("scripts/*.py")])


def code_identity() -> dict:
    hashes = {p.relative_to(REPOSITORY).as_posix(): sha256(p) for p in source_files()}

    def git(*args):
        try:
            result = subprocess.run(
                ["git", "-c", f"safe.directory={REPOSITORY.as_posix()}", *args], cwd=REPOSITORY,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    # Annotation edits and local environments are outside the runtime code identity.
    dirty = git("status", "--porcelain", "--untracked-files=all", "--", *hashes)
    return {"git_commit": git("rev-parse", "HEAD"), "git_branch": git("branch", "--show-current"),
            "dirty_source_files": dirty.splitlines() if dirty else [], "git_status_available": dirty is not None,
            "source_sha256": hashes, "source_digest": digest(hashes)}


def runtime_identity(llm) -> dict:
    from . import extract, prompts
    settings = {name: getattr(config, name) for name in SETTINGS}
    texts = {name: getattr(prompts, name) for name in
             ("EXTRACT_SYSTEM", "CONSOLIDATE_SYSTEM", "AGENT_SYSTEM", "ABSTRACT_SYSTEM")}
    texts.update({name: getattr(extract, name) for name in ("ACTION_PASS_NOTE", "CHUNK_NOTE", "B0_NOTE")})
    packages = {}
    for name in ("openai", "anthropic", "jsonschema"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    endpoint = None
    if getattr(llm, "supports_effort", False):
        parsed = urlsplit(config.OPENAI_BASE_URL)
        # Never persist URL credentials, query tokens, environment variables or API keys.
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        endpoint = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    client = llm.client
    return {"code": code_identity(),
            "model": {"name": llm.model, "client": f"{type(client).__module__}.{type(client).__name__}",
                      "endpoint": endpoint, "supports_reasoning_effort": llm.supports_effort},
            "config": settings, "config_digest": digest(settings),
            "prompts": {k: {"text": v, "sha256": digest(v)} for k, v in texts.items()},
            "environment": {"python": platform.python_version(), "packages": packages}}


class RunObservation:
    def __init__(self, txt_path: Path, mode: str, llm, out_root: Path, cache_dir: Path):
        self.started = time.monotonic()
        self.path = out_root / mode / f"{txt_path.stem}.run.json"
        self.stage_path = out_root / mode / f"{txt_path.stem}.stages.jsonl"
        run_id = uuid4().hex
        self.trace = StageTrace(self.stage_path, run_id=run_id)
        self.data = {"format_version": 1, "run_id": run_id, "meeting_id": txt_path.stem, "mode": mode,
                     "status": "RUNNING", "started_at": utc_now(), **runtime_identity(llm),
                     "input": {"path": str(txt_path.resolve()),
                               "sha256": sha256(txt_path) if txt_path.is_file() else None},
                     "context_sha256": {p.name: sha256(p) for p in
                                        (config.PARTICIPANTS, config.SCENARIO, config.SCHEMA) if p.is_file()},
                     "cache_directory": str(cache_dir.resolve()),
                     "artifacts": {"metadata": str(self.path), "stages": str(self.stage_path)}}
        self.save()
        self.trace.log(stage="run", event="started", meeting_id=txt_path.stem, mode=mode)

    def save(self):
        write_json(self.path, self.data)

    def finish(self, result: dict, usage: dict):
        self.data.update(status=result["status"], finished_at=utc_now(),
                         elapsed_seconds=round(time.monotonic() - self.started, 3), usage=usage)
        self.data["result"] = result
        self.trace.log(stage="run", event="finished", status=result["status"], usage=usage)
        self.save()
