"""Process-isolated connection to the accepted Agent; no evaluation or gold reads."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from .release import ROOT, PACKAGE, PROFILE, ReleaseMismatch, digest, load, materialize_runtime


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.05)


def connect():
    runtime = materialize_runtime()
    # Trust only this checkout for provenance commands in this child process;
    # do not edit the user's global Git configuration.
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    os.environ[f"GIT_CONFIG_KEY_{count}"] = "safe.directory"
    os.environ[f"GIT_CONFIG_VALUE_{count}"] = ROOT.as_posix()
    os.environ["GIT_CONFIG_COUNT"] = str(count + 1)
    # Match the Agent's dotenv semantics without copying credentials into a run.
    dotenv = ROOT / "agent/.env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    sys.path.insert(0, str(runtime / "agent/src"))
    from mta import config
    from mta.llm import LLM
    from mta.observation import runtime_identity
    from mta.pipeline import run_meeting
    config.QUALITY_PROFILE = PROFILE
    llm = LLM()  # constructs a client; makes no network/model request
    identity = runtime_identity(llm, "s2")
    expected = load(ROOT / PACKAGE / "study.json")["runtime"]
    for key in ("model", "config", "quality_policy"):
        if identity[key] != expected[key]:
            raise ReleaseMismatch("Accepted release mismatch: " + key + ". Check agent/.env and the release handoff.")
    if identity["code"]["source_sha256"] != expected["code"]["source_sha256"]:
        raise ReleaseMismatch("Accepted runtime code hashes do not match.")
    return llm, run_meeting, config


def execute(directory: Path, meeting_id: str):
    from .provider import visible_meetings
    sequence = visible_meetings(meeting_id)
    state = {"job_id": directory.name, "meeting_id": meeting_id, "status": "RUNNING", "stage": "preflight",
             "sequence": sequence, "completed_meetings": [], "current_meeting": sequence[0],
             "profile": PROFILE, "started_at": time.time(), "driver_sha256": digest(Path(__file__))}
    path = directory / "job.json"
    save(path, state)
    stopped = threading.Event()
    active = [None]

    def watch_cancel():
        heartbeat = 0
        while not stopped.wait(.1):
            if time.monotonic() - heartbeat > 1:
                (directory / "heartbeat").touch()
                heartbeat = time.monotonic()
            if (directory / "cancel").exists() and active[0] is not None:
                active[0].stop("cancelled", "CANCELLED")

    try:
        threading.Thread(target=watch_cancel, daemon=True).start()
        llm, pipeline, config = connect()
        scoped = llm.scoped

        def bind(control):
            active[0] = control
            if (directory / "cancel").exists():
                control.stop("cancelled", "CANCELLED")
            return scoped(control)

        # An adapter hook, outside frozen files, uses the existing public stop
        # mechanism. It changes neither prompts, acceptance gates nor budgets.
        llm.scoped = bind
        for mid in sequence:
            if (directory / "cancel").exists():
                state.update(status="CANCELLED", stage="cancelled")
                break
            state.update(current_meeting=mid, stage="parse")
            save(path, state)
            result = pipeline(config.TRANSCRIPTS / f"{mid}.txt", "s2", llm,
                              directory / "output", directory / "cache")
            save(directory / "results" / f"{mid}.json", result)
            if result["status"] != "COMPLETED":
                state.update(status=result["status"], stage=result.get("failed_stage", "failed"),
                             reason=result.get("reason", "agent_run_failed"))
                break
            state["completed_meetings"].append(mid)
        else:
            state.update(status="COMPLETED", stage="ready")
    except Exception as error:
        # Provider exceptions can contain credential fragments: never serialize
        # arbitrary exception messages, environment values, or raw stderr to UI.
        reason = str(error) if isinstance(error, ReleaseMismatch) else (
            "Install UI dependencies and configure OPENAI_API_KEY in agent/.env. "
            "Check the accepted model and settings in the handoff.")
        state.update(status="FAILED", stage="preflight", reason=reason, error_type=type(error).__name__)
    finally:
        stopped.set()
        state["finished_at"] = time.time()
        save(path, state)
    return 0 if state["status"] == "COMPLETED" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--meeting", required=True)
    args = parser.parse_args()
    from .provider import meeting_parts
    meeting_parts(args.meeting)
    directory = args.directory.resolve()
    if directory.parent != (ROOT / "agent/runs/ui").resolve():
        parser.error("Run directory must be in the UI run store.")
    return execute(directory, args.meeting)


if __name__ == "__main__":
    raise SystemExit(main())
