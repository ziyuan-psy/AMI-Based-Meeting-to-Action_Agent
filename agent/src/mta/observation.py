"""Run provenance and stage artifacts. Only explicit, non-secret settings are recorded."""
from __future__ import annotations

import hashlib
import copy
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import subprocess
import shutil
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from . import config
from .trace import StageTrace, Trace

REPOSITORY = Path(__file__).resolve().parents[3]
SETTINGS = (
    "CHUNK", "OVERLAP", "WORKERS", "EMPTY_RETRIES", "ACTION_SAMPLES", "CONSOLIDATE_SAMPLES", "MAX_ITEMS", "DECISION_RECONSOLIDATE",
    "EFFORT_EXTRACT", "EFFORT_CONSOLIDATE", "EFFORT_AGENT", "EFFORT_LINK", "EFFORT_ABSTRACT", "PRIOR_JACCARD",
    "MAX_CALLS_PER_ITEM", "MAX_CALLS_PER_MEETING", "MAX_INVALID_ACTIONS", "MAX_NO_PROGRESS",
    "MAX_FINISH_TRIES", "MAX_AGENT_STEPS", "P2_ENABLED", "LINK_POLICY_VERSION", "DOCUMENT_POLICY_VERSION",
    "SCENARIO_PASSAGE_CHARS", "SCENARIO_PASSAGE_OVERLAP", "OPENAI_MIN_COMPLETION_TOKENS",
    "MAX_MODEL_CALLS", "MAX_TOTAL_TOKENS", "REQUEST_TIMEOUT", "MEETING_TIMEOUT", "MAX_INFLIGHT",
    "TRANSPORT_RETRIES", "RETRY_BACKOFF", "INPUT_TOKEN_PADDING",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_text(path: Path, text: str, control=None) -> None:
    def write():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if control is None:
        write()
    else:
        with control.guard():
            write()


def write_json(path: Path, value, control=None) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", control)


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
             ("EXTRACT_SYSTEM", "CONSOLIDATE_SYSTEM", "DECISION_CONSOLIDATE_SYSTEM", "AGENT_SYSTEM", "P2_SYSTEM", "ABSTRACT_SYSTEM")}
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
        self.control = None
        self.agent_path = out_root / mode / f"{txt_path.stem}.trace.jsonl"
        self.attempt_dir = out_root / mode / "_attempts" / txt_path.stem / run_id
        self.attempt_dir.mkdir(parents=True, exist_ok=False)
        # Preserve earlier outputs/traces before the latest-run sidecars are replaced.
        previous = [self.path, self.stage_path, self.agent_path,
                    out_root / mode / f"{txt_path.stem}.json", out_root / mode / f"{txt_path.stem}_report.md",
                    out_root / mode / f"{txt_path.stem}.p2.json", out_root / mode / f"{txt_path.stem}.clarifications.json"]
        for path in previous:
            if path.is_file():
                target = self.attempt_dir / "previous" / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        self.trace = StageTrace(self.stage_path, run_id=run_id)
        self.agent_trace = Trace(self.agent_path)
        self.data = {"format_version": 3, "run_id": run_id, "meeting_id": txt_path.stem, "mode": mode,
                     "status": "RUNNING", "started_at": utc_now(), **runtime_identity(llm),
                     "input": {"path": str(txt_path.resolve()),
                               "sha256": sha256(txt_path) if txt_path.is_file() else None},
                     "context_sha256": {p.name: sha256(p) for p in
                                        (config.PARTICIPANTS, config.SCENARIO, config.SCHEMA, config.P2_SCHEMA) if p.is_file()},
                     "cache_directory": str(cache_dir.resolve()),
                     "checkpoints": {}, "artifacts": {"metadata": str(self.path), "stages": str(self.stage_path),
                     "agent_trace": str(self.agent_path), "attempt_directory": str(self.attempt_dir)}}
        self.save()
        self.trace.log(stage="run", event="started", meeting_id=txt_path.stem, mode=mode)

    def save(self, final=False):
        write_json(self.path, self.data, None if final else self.control)

    def enter(self, stage):
        self.control.enter(stage)
        with self.control.guard():
            self.data["current_stage"] = stage
            self.save()

    def update(self, **data):
        with self.control.guard():
            self.data.update(data)
            self.save()

    def checkpoint(self, name, value):
        with self.control.guard():
            path = self.attempt_dir / "checkpoints" / f"{name}.json"
            write_json(path, copy.deepcopy(value))
            self.data["checkpoints"][name] = str(path)
            self.save()

    def publish(self, meeting_id, record, report, extra=None):
        """Only the supervisor publishes validated results; restore old bytes on any failure."""
        record_path = self.path.parent / f"{meeting_id}.json"
        report_path = self.path.parent / f"{meeting_id}_report.md"
        additional = {self.path.parent / f"{meeting_id}.{name}.json": value for name, value in (extra or {}).items()}
        with self.control.guard():
            old = {p: p.read_bytes() if p.is_file() else None for p in (record_path, report_path, *additional)}
            try:
                write_json(record_path, record)
                write_text(report_path, report)
                for path, value in additional.items():
                    write_json(path, value)
                self.control.check("publish")
            except BaseException:
                for path, contents in old.items():
                    if contents is None:
                        path.unlink(missing_ok=True)
                    else:
                        temporary = path.with_name(f".{path.name}.{uuid4().hex}.rollback")
                        temporary.write_bytes(contents)
                        os.replace(temporary, path)
                raise
        return str(record_path)

    def finish(self, result: dict, usage: dict):
        self.agent_trace.close()
        self.data.update(status=result["status"], finished_at=utc_now(),
                         elapsed_seconds=round(time.monotonic() - self.started, 3), usage=usage)
        self.data["result"] = result
        if self.control is not None:
            self.data["runtime_control"] = self.control.snapshot()
        self.trace.finish(stage="run", event="finished", status=result["status"], usage=usage)
        if result["status"] != "COMPLETED":
            partial = self.attempt_dir / "partial.json"
            p2_items = []
            for name, checkpoint in self.data["checkpoints"].items():
                if name.startswith("p2_item_"):
                    outcome = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
                    if outcome["state"] == "running":
                        outcome.update(state="interrupted", termination_reason=result.get("reason", result["status"]))
                    p2_items.append(outcome)
            if p2_items:
                for name, value in (("p2", {"items": p2_items}),
                                    ("clarifications", {"requests": [r for i in p2_items for r in i["clarifications"]]})):
                    path = self.attempt_dir / f"{name}.partial.json"
                    write_json(path, {"format_version": 1, "run_id": self.data["run_id"], "published": False,
                                      "status": result["status"], **value})
                    result[name + "_partial_artifact"] = str(path)
            write_json(partial, {"run_id": self.data["run_id"], "status": result["status"],
                                "failure": result.get("failure", {"code": result.get("reason")}),
                                "checkpoints": self.data["checkpoints"],
                                "p2": result.get("p2_partial_artifact"),
                                "clarifications": result.get("clarifications_partial_artifact"),
                                "stages": str(self.attempt_dir / "stages.jsonl"),
                                "agent_trace": str(self.attempt_dir / "agent.trace.jsonl")})
            result["partial_artifact"] = str(partial)
        write_json(self.attempt_dir / "run.json", self.data)
        shutil.copyfile(self.stage_path, self.attempt_dir / "stages.jsonl")
        shutil.copyfile(self.agent_path, self.attempt_dir / "agent.trace.jsonl")
        # The latest terminal metadata is the commit marker used by the series runner.
        # Do not expose COMPLETED until the attempt's required artifacts are durable.
        self.save(final=True)
