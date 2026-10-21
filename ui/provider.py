"""Data boundary for the UI; no model clients, credentials, or gold labels are read.

The final agent should implement MeetingProvider (see ui/README.md). Transport and
presentation consume RunBundle, never mta internals or the on-disk replay layout.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import uuid
import warnings
from collections import Counter, OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from jsonschema import Draft202012Validator
from markdown_it import MarkdownIt

from transcript_parser import parse_transcript_file

ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_SERIES = ("ES2011", "IS1008", "TS3004", "TS3006")
SCHEMA_VERSION = "0.5.0"


class DataError(Exception):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def meeting_parts(meeting_id: str) -> tuple[str, int]:
    match = re.fullmatch(r"(ES2011|IS1008|TS3004|TS3006)([a-d])", meeting_id)
    if not match:
        raise DataError("Choose a meeting from the development catalog.", "meeting_not_found", 404)
    return match[1], "abcd".index(match[2])


def visible_meetings(meeting_id: str) -> list[str]:
    series, order = meeting_parts(meeting_id)
    return [series + letter for letter in "abcd"[:order + 1]]


def render_markdown(markdown: str) -> str:
    # HTML in transcripts is text, not markup. markdown-it also rejects unsafe
    # link protocols. No remote assets, scripts, or HTML plugins are enabled.
    return MarkdownIt("commonmark", {"html": False}).enable("table").render(markdown)


@dataclass(frozen=True)
class RunBundle:
    """UI envelope; ``items`` inside payload retain the exact gold-schema shape."""

    payload: dict
    record_text: str
    report_markdown: str


class MeetingProvider(Protocol):
    def catalog(self) -> dict: ...
    def meeting(self, meeting_id: str) -> dict: ...
    def start_run(self, meeting_id: str) -> RunBundle: ...
    def get_run(self, run_id: str) -> RunBundle: ...


class SavedDevelopmentProvider:
    """Read-only S2 snapshot, restricted to SB and current/earlier same-series data."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        self.results = root / "agent" / "results" / "mvp" / "s2"
        # Read the schema, not any annotation JSON. The agent copy is checked in tests.
        self.schema = json.loads((root / "revised_gold_output" / "revised_gold schema template.json").read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(self.schema)
        self.participants = json.loads((root / "agent" / "data" / "participants.json").read_text(encoding="utf-8"))
        self._runs: OrderedDict[str, RunBundle] = OrderedDict()
        self._lock = threading.Lock()

    def catalog(self) -> dict:
        meetings = []
        for series in DEVELOPMENT_SERIES:
            for order, letter in enumerate("abcd"):
                mid = series + letter
                meetings.append({"id": mid, "series": series, "order": order + 1,
                                 "has_saved_run": (self.results / f"{mid}.json").is_file(),
                                 "prior_meeting_ids": visible_meetings(mid)[:-1]})
        return {"series": list(DEVELOPMENT_SERIES), "meetings": meetings,
                "schema_version": SCHEMA_VERSION, "source": "saved_development"}

    @lru_cache(maxsize=16)
    def meeting(self, meeting_id: str) -> dict:
        series, order = meeting_parts(meeting_id)
        path = self.root / "transcripts" / "SB" / f"{meeting_id}.txt"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            acts = parse_transcript_file(path, expected_meeting=meeting_id)
        roster = self.participants[meeting_id]
        people = [{"speaker": speaker, "id": p["participant"], "role": p["role"]}
                  for speaker, p in sorted(roster.items())]
        # Enrichment is separate from the parser, matching the agent's input flow.
        for act in acts:
            p = roster[act["speaker"]]
            act["attributes"] = {"participant": p["participant"], "role": p["role"]}
        return {"id": meeting_id, "series": series, "order": order + 1,
                "participants": people, "dialogue_acts": acts,
                "duration_seconds": max(float(a["endtime"]) for a in acts),
                "prior_meeting_ids": visible_meetings(meeting_id)[:-1],
                "has_saved_run": (self.results / f"{meeting_id}.json").is_file(),
                "parser_warnings": [str(w.message) for w in caught]}

    def _record(self, meeting_id: str) -> tuple[list[dict], str]:
        meeting_parts(meeting_id)
        path = self.results / f"{meeting_id}.json"
        if not path.is_file():
            raise DataError("No saved agent record is available for this meeting yet.", "saved_run_unavailable", 404)
        raw = path.read_text(encoding="utf-8")
        items = json.loads(raw)
        errors = list(self.validator.iter_errors(items))
        if errors:
            raise DataError(f"Saved record failed schema validation: {errors[0].message}", "schema_invalid", 422)
        if len({i["id"] for i in items}) != len(items) or any(not i["id"].startswith(meeting_id + ".") for i in items):
            raise DataError("Saved record has duplicate or mismatched item IDs.", "record_invalid", 422)
        return items, raw

    def _resolve(self, locator: str, current: str, prior: dict[str, dict], source_type: str = "dialogue") -> dict:
        """Resolve only typed locators, never a client-supplied filesystem path."""
        unavailable = {"kind": "unavailable", "locator": locator, "text": "Source unavailable in this saved run."}
        allowed = visible_meetings(current)
        if source_type == "project_document":
            match = re.fullmatch(r"PDF page (\d+)(?: \(one-based\))?", locator)
            if match:
                raw = (self.root / "agent" / "data" / "scenario_text.txt").read_text(encoding="utf-8")
                parts = re.split(r"=== PDF PAGE (\d+) ===", raw)
                for page, text in zip(parts[1::2], parts[2::2]):
                    if int(page) == int(match[1]):
                        return {"kind": "document", "locator": locator, "text": text.strip(), "page": int(page)}
            return unavailable
        da_id = locator.split(" ")[0]
        mid = da_id.split(".")[0]
        if mid not in allowed:
            return unavailable
        acts = self.meeting(mid)["dialogue_acts"]
        for index, act in enumerate(acts):
            if act["id"] == da_id:
                context = [{"id": a["id"], "text": a["text"], "speaker": a["speaker"],
                            "role": a["attributes"]["role"], "participant": a["attributes"]["participant"],
                            "starttime": a["starttime"], "highlight": a["id"] == da_id}
                           for a in acts[max(0, index - 2):index + 3]]
                return {"kind": "participant" if source_type == "participant_metadata" else "dialogue",
                        "locator": locator, "meeting_id": mid, "text": act["text"],
                        "role": act["attributes"]["role"], "participant": act["attributes"]["participant"],
                        "starttime": act["starttime"], "context": context}
        return unavailable

    def _observation(self, locator: str, current: str, prior: dict, event: dict) -> dict:
        if locator in prior:
            item = prior[locator]
            return {"kind": "record", "locator": locator, "meeting_id": locator.split(".")[0],
                    "text": item["text"], "item": item}
        if event.get("action") == "get_participants":
            mid = event.get("args", {}).get("meeting_id", current)
            if mid in visible_meetings(current):
                person = next((p for p in self.meeting(mid)["participants"] if p["id"] == locator), None)
                if person:
                    return {"kind": "participant", "locator": locator, "meeting_id": mid,
                            "role": person["role"], "participant": locator, "text": f"{person['speaker']} → {person['role']} ({locator})"}
        if re.fullmatch(r"p\d+", locator) and event.get("action") == "search_project_doc":
            return self._resolve(f"PDF page {locator[1:]} (one-based)", current, prior, "project_document")
        return self._resolve(locator, current, prior)

    def start_run(self, meeting_id: str) -> RunBundle:
        meeting_parts(meeting_id)
        items, raw = self._record(meeting_id)
        earlier = []
        for mid in visible_meetings(meeting_id)[:-1]:
            if (self.results / f"{mid}.json").is_file():
                previous, _ = self._record(mid)
                earlier.append({"meeting_id": mid, "items": previous})
        prior = {i["id"]: i for record in earlier for i in record["items"] if i["type"] != "abstract"}
        # Fail closed on links into unavailable, future, or other-series records.
        if any(r["id"] not in prior for i in items for r in i.get("related_items", [])):
            raise DataError("A saved relationship points outside available earlier records.", "relationship_invalid", 422)
        current = self.meeting(meeting_id)
        evidence = {}
        for item in items:
            for ref in item["reference"]:
                evidence[ref["source_type"] + ":" + ref["locator"]] = self._resolve(ref["locator"], meeting_id, prior, ref["source_type"])
        trace_path = self.results / f"{meeting_id}.trace.jsonl"
        trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        events = []
        for original in trace:
            event = copy.deepcopy(original)
            # Existing traces use meeting#<zero-based record position>. The live
            # adapter should emit record_id directly rather than rely on this map.
            match = re.fullmatch(re.escape(meeting_id) + r"#(\d+)", event.get("item", ""))
            index = int(match[1]) if match else -1
            event["record_id"] = items[index]["id"] if 0 <= index < len(items) and items[index]["type"] != "abstract" else None
            event["observations"] = [self._observation(loc, meeting_id, prior, event) for loc in event.get("observation_ids", [])]
            event["observation_source"] = "resolved_logged_ids"
            events.append(event)
        markdown = (self.results / f"{meeting_id}_report.md").read_text(encoding="utf-8")
        unresolved = [i["id"] for i in items if i["type"] == "actions" and
                      (i["owner_basis"] in ("ambiguous", "missing") or i["deadline_basis"] == "ambiguous")]
        people = {mid: self.meeting(mid)["participants"] for mid in visible_meetings(meeting_id)}
        run_id = uuid.uuid4().hex
        payload = {"run_id": run_id, "meeting_id": meeting_id, "source": "saved_development", "mode": "s2",
                   "status": "COMPLETED", "stage": "ready", "schema_version": SCHEMA_VERSION, "schema_valid": True,
                   "review_state": "unreviewed", "items": items, "earlier_records": earlier, "events": events,
                   "evidence": evidence, "participants": people, "unresolved_item_ids": unresolved,
                   "counts": dict(Counter(i["type"] for i in items)),
                   "report_markdown": markdown, "report_html": render_markdown(markdown),
                   "provenance": {"record": f"agent/results/mvp/s2/{meeting_id}.json",
                                  "trace": f"agent/results/mvp/s2/{meeting_id}.trace.jsonl"},
                   "warnings": current["parser_warnings"]}
        bundle = RunBundle(payload, raw, markdown)
        with self._lock:
            self._runs[run_id] = bundle
            while len(self._runs) > 32:
                self._runs.popitem(last=False)
        return bundle

    def get_run(self, run_id: str) -> RunBundle:
        with self._lock:
            bundle = self._runs.get(run_id)
        if bundle is None:
            raise DataError("This saved-run session expired. Open the meeting record again.", "run_not_found", 404)
        return bundle
