"""Offline connection tests: real acceptance artifacts, simulated job execution."""
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from ui import agent_worker
from ui.jobs import LiveJobs
from ui.provider import ROOT, DataError, SavedDevelopmentProvider
from ui.release import PACKAGE, PROFILE, digest, load, materialize_runtime


class Process:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


class JobTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "agent/runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="ui-test-", dir=ROOT / "agent/runs")
        self.process = Process()
        self.jobs = LiveJobs(process_factory=lambda *args, **kw: self.process)
        self.jobs.directory = Path(self.temp.name)
        self.jobs.capabilities = lambda: {"available": True}
        self.jobs._supervise = lambda job_id: None

    def tearDown(self):
        self.process.terminate()
        self.temp.cleanup()

    def test_start_uses_fresh_chronological_history_and_blocks_second_job(self):
        state = self.jobs.start("TS3006c")
        self.assertEqual(state["sequence"], ["TS3006a", "TS3006b", "TS3006c"])
        self.assertEqual(state["status"], "QUEUED")
        self.assertFalse((self.jobs.directory / state["job_id"] / "output").exists())
        with self.assertRaises(DataError) as caught:
            self.jobs.start("TS3006a")
        self.assertEqual(caught.exception.code, "run_active")

    def test_held_out_live_requests_are_rejected(self):
        with self.assertRaises(DataError):
            self.jobs.start("ES2004a")
        with self.assertRaises(DataError):
            self.jobs.start("TS3007a")

    def test_cancel_signals_only_its_worker_and_incomplete_export_is_blocked(self):
        state = self.jobs.start("TS3006a")
        cancelled = self.jobs.cancel(state["job_id"])
        self.assertTrue(cancelled["cancel_requested"])
        self.assertTrue((self.jobs.directory / state["job_id"] / "cancel").exists())
        with self.assertRaises(DataError) as caught:
            self.jobs.bundle(state["job_id"])
        self.assertEqual(caught.exception.status, 409)

    def test_crashed_worker_is_a_failure_not_a_completed_run(self):
        state = self.jobs.start("TS3006a")
        self.process.returncode = 1
        self.assertEqual(self.jobs.get(state["job_id"])["status"], "FAILED")

    def test_completed_job_uses_own_artifacts_and_unchanged_json_export(self):
        state = self.jobs.start("TS3006b")
        directory = self.jobs.directory / state["job_id"]
        output = directory / "output/s2"
        output.mkdir(parents=True)
        source = ROOT / PACKAGE / "run/s2"
        for mid in ("TS3006a", "TS3006b"):
            for suffix in (".json", "_report.md", ".run.json", ".p2.json", ".clarifications.json", ".trace.jsonl", ".stages.jsonl"):
                shutil.copyfile(source / (mid + suffix), output / (mid + suffix))
        state.update(status="COMPLETED", current_meeting="TS3006b", completed_meetings=state["sequence"], finished_at=time.time())
        agent_worker.save(directory / "job.json", state)
        self.process.returncode = 0
        bundle = self.jobs.bundle(state["job_id"])
        self.assertEqual(bundle.payload["source"], "live_agent")
        self.assertEqual(bundle.payload["run_id"], state["job_id"])
        self.assertEqual(json.loads(bundle.record_text), load(output / "TS3006b.json"))
        self.assertEqual([r["meeting_id"] for r in bundle.payload["earlier_records"]], ["TS3006a"])

    def test_artifact_tampering_is_not_published(self):
        state = self.jobs.start("TS3006a")
        directory = self.jobs.directory / state["job_id"]
        output = directory / "output/s2"
        output.mkdir(parents=True)
        source = ROOT / PACKAGE / "run/s2"
        for suffix in (".json", "_report.md", ".run.json", ".p2.json", ".clarifications.json"):
            shutil.copyfile(source / ("TS3006a" + suffix), output / ("TS3006a" + suffix))
        (output / "TS3006a.json").write_text("[]")
        state.update(status="COMPLETED", current_meeting="TS3006a", finished_at=time.time())
        agent_worker.save(directory / "job.json", state)
        self.process.returncode = 0
        with self.assertRaises(DataError) as caught:
            self.jobs.bundle(state["job_id"])
        self.assertEqual(caught.exception.code, "artifact_invalid")


class ReleaseTests(unittest.TestCase):
    def test_preflight_errors_do_not_expose_provider_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch("ui.agent_worker.connect", side_effect=RuntimeError("synthetic-secret-do-not-expose")):
                agent_worker.execute(directory, "TS3006a")
            state = load(directory / "job.json")
            self.assertEqual(state["status"], "FAILED")
            self.assertNotIn("synthetic-secret", json.dumps(state))

    def test_private_runtime_preserves_frozen_bytes_and_excludes_held_out_inputs(self):
        runtime = materialize_runtime()
        study = load(ROOT / PACKAGE / "study.json")
        for relative, expected in study["source_sha256"].items():
            self.assertEqual(digest(runtime / relative), expected)
        self.assertEqual(len(list((runtime / "agent/data/transcripts").glob("*.txt"))), 16)
        self.assertFalse((runtime / "agent/data/transcripts/ES2004a.txt").exists())
        self.assertFalse((runtime / "agent/.env").exists())

    def test_trace_map_is_authoritative_even_when_a_candidate_has_no_record(self):
        from ui.release import json_lines as actual
        def changed(path):
            events = actual(path)
            if path.name == "TS3006a.stages.jsonl":
                for event in events:
                    if event.get("stage") == "assemble":
                        event["item_record_map"]["TS3006a#7"] = None
            return events
        with patch("ui.provider.json_lines", side_effect=changed):
            run = SavedDevelopmentProvider().start_run("TS3006a").payload
        self.assertIsNone(next(e for e in run["events"] if e["item"] == "TS3006a#7")["record_id"])

    def test_delivered_results_preserve_logged_order_instead_of_sorted_ids(self):
        run = SavedDevelopmentProvider().start_run("TS3006b").payload
        event = next(e for e in run["events"] if e.get("action") == "get_prior_items" and e.get("delivered_result"))
        actual = json.loads(event["delivered_result"])["results"]
        self.assertEqual([e["locator"] for e in event["observations"]], [e["id"] for e in actual])
        self.assertEqual([e["text"] for e in event["observations"]], [e["text"] for e in actual])

    def test_disabled_p2_is_not_reported_as_all_checks_passed(self):
        run = SavedDevelopmentProvider().start_run("TS3006d").payload
        self.assertFalse(run["workflow"]["p2_enabled"])
        self.assertEqual(run["workflow"]["policy"]["assurance"], "targeted_follow_up_not_full_p2")
        self.assertEqual(run["review_state"], "draft_candidate")

    def test_worker_runs_each_prerequisite_and_stops_after_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            calls = []
            def pipeline(path, mode, llm, out, cache):
                calls.append(path.stem)
                return {"status": "BLOCKED", "failed_stage": "validate", "reason": "validation_failed"}
            client = SimpleNamespace(scoped=lambda control: control)
            config = SimpleNamespace(TRANSCRIPTS=Path("transcripts"))
            with patch("ui.agent_worker.connect", return_value=(client, pipeline, config)):
                self.assertEqual(agent_worker.execute(directory, "TS3006c"), 1)
            self.assertEqual(calls, ["TS3006a"])
            self.assertEqual(load(directory / "job.json")["status"], "BLOCKED")

    def test_cancellation_reaches_existing_agent_controller(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cancelled = threading.Event()
            control = SimpleNamespace(stop=lambda code, status: cancelled.set())
            def pipeline(path, mode, llm, out, cache):
                llm.scoped(control)
                (directory / "cancel").touch()
                self.assertTrue(cancelled.wait(2))
                return {"status": "CANCELLED", "reason": "cancelled"}
            client = SimpleNamespace(scoped=lambda control: control)
            with patch("ui.agent_worker.connect", return_value=(client, pipeline, SimpleNamespace(TRANSCRIPTS=Path("transcripts")))):
                agent_worker.execute(directory, "TS3006b")
            self.assertEqual(load(directory / "job.json")["status"], "CANCELLED")


class LiveTransportTests(unittest.TestCase):
    setUp = JobTests.setUp
    tearDown = JobTests.tearDown

    def test_async_start_poll_cancel_and_export_gate_over_http(self):
        from ui.server import make_server
        server = make_server(port=0, jobs=self.jobs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            data = json.dumps({"meeting_id": "TS3006b"}).encode()
            with urlopen(Request(base + "/api/agent/runs", data=data, headers={"Content-Type": "application/json"})) as response:
                self.assertEqual(response.status, 202)
                job = json.load(response)
            with urlopen(base + "/api/agent/runs/" + job["job_id"]) as response:
                self.assertEqual(json.load(response)["status"], "QUEUED")
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + "/api/runs/" + job["job_id"] + "/export?format=json")
            self.assertEqual(caught.exception.code, 409)
            with urlopen(Request(base + "/api/agent/runs/" + job["job_id"] + "/cancel", data=b"{}", headers={"Content-Type": "application/json"})) as response:
                self.assertTrue(json.load(response)["cancel_requested"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
