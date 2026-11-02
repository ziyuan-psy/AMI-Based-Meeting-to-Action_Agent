"""Asynchronous live runs: durable IDs, one active job, isolated history and cancellation."""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .agent_worker import save
from .provider import ROOT, DataError, RunBundle, SavedDevelopmentProvider, visible_meetings
from .release import PROFILE, REFERENCE, json_lines, load

TERMINAL = {"COMPLETED", "FAILED", "BLOCKED", "TIMED_OUT", "BUDGET_EXCEEDED", "CANCELLED"}


class LiveJobs:
    def __init__(self, root: Path = ROOT, process_factory=subprocess.Popen):
        self.root = root
        self.directory = root / "agent/runs/ui"
        self.process_factory = process_factory
        self.processes = {}
        self.bundles = {}
        self.lock = threading.RLock()

    def capabilities(self):
        sdk = importlib.util.find_spec("openai") is not None
        configured = bool(os.environ.get("OPENAI_API_KEY")) or (self.root / "agent/.env").is_file()
        return {"profile": PROFILE, "reference": REFERENCE, "sdk_installed": sdk,
                "credentials_configured": configured, "available": sdk and configured,
                "p2_enabled": False, "history_policy": "fresh_same_series_chronological",
                "limits_per_meeting": {"calls": 80, "tokens": 1000000, "seconds": 1200},
                "message": "Live runs call the configured model. Earlier meetings are processed first in a fresh run. Full P2 is disabled for this accepted candidate."}

    def _directory(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise DataError("Run not found.", "run_not_found", 404)
        path = self.directory / job_id
        if not (path / "job.json").is_file():
            raise DataError("Run not found.", "run_not_found", 404)
        return path

    def start(self, meeting_id):
        sequence = visible_meetings(meeting_id)
        capabilities = self.capabilities()
        if not capabilities["available"]:
            raise DataError("Live agent setup is incomplete. Install ui/requirements.txt and set OPENAI_API_KEY in agent/.env. Saved previews remain available.", "agent_not_configured", 409)
        with self.lock:
            if self.directory.exists():
                for saved in self.directory.glob("*/job.json"):
                    existing = self.get(saved.parent.name)
                    if existing["status"] not in TERMINAL:
                        raise DataError("An agent run is already active. Reopen its run link or wait for completion.", "run_active", 409)
            for job_id, process in self.processes.items():
                if process.poll() is None:
                    raise DataError("An agent run is already active. Wait for it to finish or cancel it.", "run_active", 409)
            job_id = uuid.uuid4().hex
            directory = self.directory / job_id
            state = {"job_id": job_id, "meeting_id": meeting_id, "status": "QUEUED", "stage": "queued",
                     "sequence": sequence, "completed_meetings": [], "current_meeting": sequence[0],
                     "profile": PROFILE, "started_at": time.time()}
            save(directory / "job.json", state)
            try:
                with (directory / "worker.log").open("w", encoding="utf-8") as log:
                    self.processes[job_id] = self.process_factory(
                        [sys.executable, "-X", "utf8", "-m", "ui.agent_worker", "--meeting", meeting_id,
                         "--directory", str(directory)], cwd=self.root, stdout=log, stderr=log,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                state.update(status="FAILED", reason="The agent worker could not start.")
                save(directory / "job.json", state)
                raise DataError("The agent worker could not start.", "worker_failed", 500) from None
            threading.Thread(target=self._supervise, args=(job_id,), daemon=True).start()
        return self.get(job_id)

    def _supervise(self, job_id):
        process = self.processes[job_id]
        directory = self.directory / job_id
        state = load(directory / "job.json")
        deadline = time.monotonic() + len(state["sequence"]) * 1200 + 60
        cancel_since = None
        while process.poll() is None:
            if (directory / "cancel").exists():
                cancel_since = cancel_since or time.monotonic()
            if time.monotonic() > deadline or (cancel_since and time.monotonic() - cancel_since > 10):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                state = load(directory / "job.json")
                state.update(status="CANCELLED" if cancel_since else "TIMED_OUT",
                             reason="Worker stopped. Existing checkpoints and completed earlier meetings were preserved.",
                             finished_at=time.time())
                save(directory / "job.json", state)
                break
            time.sleep(.2)

    def get(self, job_id):
        directory = self._directory(job_id)
        state = load(directory / "job.json")
        process = self.processes.get(job_id)
        if process is None and state["status"] not in TERMINAL:
            heartbeat = directory / "heartbeat"
            last_seen = heartbeat.stat().st_mtime if heartbeat.exists() else state["started_at"]
            if time.time() - last_seen > 30:
                state.update(status="FAILED", reason="The agent worker was interrupted. Completed artifacts and checkpoints remain saved.", finished_at=time.time())
                save(directory / "job.json", state)
        if process is not None and process.poll() is not None and state["status"] not in TERMINAL:
            state.update(status="FAILED", reason="Agent worker stopped before publishing a completed result.")
            save(directory / "job.json", state)
        state["elapsed_seconds"] = round((state.get("finished_at") or time.time()) - state["started_at"], 1)
        state["cancel_requested"] = (directory / "cancel").exists() and state["status"] not in TERMINAL
        mid = state["current_meeting"]
        output = directory / "output/s2"
        metadata_path = output / f"{mid}.run.json"
        if metadata_path.is_file():
            try:
                metadata = load(metadata_path)
                state["stage"] = metadata.get("current_stage", state["stage"])
                state["meeting_elapsed_seconds"] = metadata.get("elapsed_seconds")
                state["usage"] = metadata.get("usage", {})
            except (ValueError, OSError):
                pass
        stages = json_lines(output / f"{mid}.stages.jsonl")
        state["stages"] = [{k: e[k] for k in ("seq", "t", "stage", "event", "current_stage", "status") if k in e} for e in stages][-100:]
        if state["status"] == "COMPLETED":
            state["stage"] = "ready"
            state["run_id"] = job_id
        return state

    def bundle(self, job_id):
        state = self.get(job_id)
        if state["status"] != "COMPLETED":
            raise DataError("This run has not published a completed record.", "run_incomplete", 409)
        with self.lock:
            if job_id not in self.bundles:
                directory = self.directory / job_id
                provider = SavedDevelopmentProvider(self.root, directory / "output/s2", "live_agent")
                bundle = provider.start_run(state["meeting_id"])
                bundle.payload["run_id"] = job_id
                self.bundles[job_id] = RunBundle(bundle.payload, bundle.record_text, bundle.report_markdown)
            return self.bundles[job_id]

    def cancel(self, job_id):
        directory = self._directory(job_id)
        state = self.get(job_id)
        if state["status"] not in TERMINAL:
            (directory / "cancel").touch()
            state["cancel_requested"] = True
        return state

    def close(self):
        for job_id, process in list(self.processes.items()):
            if process.poll() is None:
                self.cancel(job_id)
