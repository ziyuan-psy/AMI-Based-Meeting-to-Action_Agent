"""Offline integration checks using real SB fixtures; no model or gold-label access."""
import copy
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ui.provider import ROOT, DataError, SavedDevelopmentProvider, render_markdown
from ui.server import make_server
from ui.release import json_lines


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = SavedDevelopmentProvider()

    def test_catalog_contains_all_and_only_development_meetings(self):
        meetings = self.provider.catalog()["meetings"]
        self.assertEqual(len(meetings), 16)
        self.assertEqual({m["id"][:-1] for m in meetings}, {"ES2011", "IS1008", "TS3004", "TS3006"})
        self.assertEqual(sum(m["has_saved_run"] for m in meetings), 4)
        for mid in ("ES2004a", "TS3007d", "../.env", "TS3006e"):
            with self.assertRaises(DataError):
                self.provider.meeting(mid)

    def test_all_saved_records_preserve_canonical_schema_and_data(self):
        schema_copy = json.loads((ROOT / "agent/schemas/output_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(self.provider.schema, schema_copy)
        for letter in "abcd":
            bundle = self.provider.start_run("TS3006" + letter)
            expected = json.loads((self.provider.results / f"TS3006{letter}.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle.payload["items"], expected)
            self.assertEqual(json.loads(bundle.record_text), expected)
            self.provider.validator.validate(bundle.payload["items"])
            self.assertEqual(bundle.payload["source"], "saved_acceptance")
            self.assertTrue(bundle.payload["schema_valid"])
            self.assertFalse(bundle.payload["workflow"]["p2_enabled"])

    def test_parser_and_participant_enrichment(self):
        meeting = self.provider.meeting("TS3006b")
        self.assertGreater(len(meeting["dialogue_acts"]), 1000)
        self.assertEqual(len(meeting["participants"]), 4)
        first = meeting["dialogue_acts"][0]
        self.assertIsNone(first["label"])
        self.assertIn("participant", first["attributes"])
        self.assertEqual(set(first), {"id", "speaker", "starttime", "endtime", "text", "attributes", "label", "startwordid", "endwordid"})

    def test_every_catalog_transcript_has_complete_participant_mapping(self):
        for entry in self.provider.catalog()["meetings"]:
            meeting = self.provider.meeting(entry["id"])
            roster = {p["id"] for p in meeting["participants"]}
            self.assertTrue(meeting["dialogue_acts"])
            self.assertTrue(all(a["id"].startswith(entry["id"] + ".") and a["attributes"]["participant"] in roster
                                for a in meeting["dialogue_acts"]))

    def test_later_meeting_has_only_earlier_records_and_logged_observations(self):
        run = self.provider.start_run("TS3006c").payload
        self.assertEqual([r["meeting_id"] for r in run["earlier_records"]], ["TS3006a", "TS3006b"])
        assembly = next(e for e in json_lines(self.provider.results / "TS3006c.stages.jsonl") if e.get("stage") == "assemble")
        self.assertEqual(run["events"][0]["record_id"], assembly["item_record_map"][run["events"][0]["item"]])
        self.assertEqual(run["events"][0]["observation_source"], "delivered_tool_result")
        self.assertEqual(run["events"][0]["observations"], [])  # actual first lookup returned no matches
        self.assertTrue(any(e["observations"] for e in run["events"]))
        for event in run["events"]:
            for observation in event["observations"]:
                if "meeting_id" in observation:
                    self.assertIn(observation["meeting_id"], ("TS3006a", "TS3006b", "TS3006c"))
        previous = {i["id"]: i for record in run["earlier_records"] for i in record["items"]}
        for item in run["items"]:
            for relation in item.get("related_items", []):
                self.assertIn(relation["id"], previous)

    def test_reference_resolution_blocks_future_and_other_series(self):
        for locator in ("TS3006d.A.dialog-act.dharshi.1", "ES2011a.A.dialog-act.dharshi.1", "ES2004a.A.dialog-act.dharshi.1"):
            self.assertEqual(self.provider._resolve(locator, "TS3006b", {})["kind"], "unavailable")
        run = self.provider.start_run("TS3006c").payload
        evidence = next(e for e in run["evidence"].values() if e["kind"] == "dialogue")
        self.assertEqual(evidence["kind"], "dialogue")
        self.assertEqual(sum(line["highlight"] for line in evidence["context"]), 1)

    def test_missing_saved_output_is_explicit(self):
        with self.assertRaises(DataError) as caught:
            self.provider.start_run("ES2011a")
        self.assertEqual(caught.exception.code, "saved_run_unavailable")

    def test_schema_rejects_ui_fields_on_items(self):
        item = copy.deepcopy(self.provider.start_run("TS3006a").payload["items"][0])
        item["run_id"] = "ui-field"
        self.assertTrue(list(self.provider.validator.iter_errors([item])))

    def test_markdown_renders_tables_and_escapes_html(self):
        html = render_markdown("# Record\n\n| Task | Owner |\n|---|---|\n| Do work | PM |\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))")
        self.assertIn("<table>", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def open_run(self, mid="TS3006c"):
        return urlopen(Request(self.base + "/api/runs", data=json.dumps({"meeting_id": mid}).encode(), headers={"Content-Type": "application/json"}))

    def test_static_page_and_assets_are_served(self):
        for path in ("/", "/app.js", "/styles.css", "/favicon.svg"):
            with urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("nosniff", response.headers["X-Content-Type-Options"])
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/agent/.env")
        self.assertEqual(caught.exception.code, 404)

    def test_run_and_downloads_match_original_artifacts(self):
        with self.open_run() as response:
            self.assertEqual(response.status, 201)
            run = json.load(response)
        for fmt, filename in (("json", "TS3006c.json"), ("markdown", "TS3006c_report.md")):
            with urlopen(f"{self.base}/api/runs/{run['run_id']}/export?format={fmt}") as response:
                self.assertIn(filename, response.headers["Content-Disposition"])
                original = (SavedDevelopmentProvider().results / filename).read_text(encoding="utf-8")
                self.assertEqual(response.read().decode(), original)

    def test_unknown_held_out_and_malformed_requests_are_rejected(self):
        for path in ("/api/meetings/TS3007a", "/api/meetings/ES2004a", "/api/meetings/../../.env", "/api/runs/" + "0" * 32):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.base + path)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.base + "/api/runs", data=b"[]"))
        self.assertEqual(caught.exception.code, 400)

    def test_cross_origin_post_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.base + "/api/runs", data=b'{"meeting_id":"TS3006c"}', headers={"Origin": "https://example.org"}))
        self.assertEqual(caught.exception.code, 403)

    def test_nonlocal_host_cannot_access_the_live_capability_endpoint(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.base + "/api/agent", headers={"Host": "example.org"}))
        self.assertEqual(caught.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
