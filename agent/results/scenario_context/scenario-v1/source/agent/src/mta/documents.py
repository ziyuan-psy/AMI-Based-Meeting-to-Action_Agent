"""Immutable scenario passages and validated, explicitly selected document evidence. No gold access."""
from __future__ import annotations

import copy
import hashlib
import re

from . import config

SELECTION = {"type": "object", "properties": {
    "passage_id": {"type": "string"},
    "result": {"enum": ["supports", "context", "irrelevant", "contradicts", "uncertain"]},
    "reason": {"type": "string", "minLength": 1}},
    "required": ["passage_id", "result", "reason"], "additionalProperties": False}


class ScenarioDocument:
    source = "amiScenario.pdf"

    def __init__(self, path, index_class, tokenizer):
        raw = path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        parts = re.split(r"=== PDF PAGE (\d+) ===", text)
        self.pages = {}
        for number, body in zip(parts[1::2], parts[2::2]):
            page = int(number)
            if page <= 0 or page in self.pages:
                raise ValueError("Invalid or duplicate scenario page")
            self.pages[page] = body.strip()
        self.passages = []
        size, overlap = config.SCENARIO_PASSAGE_CHARS, config.SCENARIO_PASSAGE_OVERLAP
        if size < 100 or not 0 <= overlap < size:
            raise ValueError("Invalid scenario passage configuration")
        for page, body in self.pages.items():
            start = 0
            while start < len(body):
                end = min(start + size, len(body))
                if end < len(body):
                    # Prefer a complete paragraph/line, retaining an exact canonical text range.
                    boundary = body.rfind("\n", start + size // 2, end)
                    if boundary >= 0:
                        end = boundary + 1
                self.passages.append(self.row(page, start, end, "search"))
                if end == len(body):
                    break
                start = max(start + 1, end - overlap)
        self.index = index_class([p["text"] for p in self.passages], tokenizer=tokenizer)

    def row(self, page, start, end, origin):
        body = self.pages[page]
        metadata = {}
        for name in ("Time", "Role", "Subject"):
            match = re.search(r"^" + name + r":[ \t]*([^\n]*)", body, re.M)
            metadata[name.lower()] = match.group(1).strip() if match else None
        return {"kind": "project_document", "passage_id": f"scenario:p{page}:{start}-{end}",
                "source": self.source, "source_sha256": self.sha256, "page": page,
                "start": start, "end": end, "text": body[start:end], "origin": origin,
                "page_length": len(body), "next_offset": end if end < len(body) else None,
                "page_metadata": metadata}

    def search(self, query, k=3):
        return [{**self.passages[i], "score": round(score, 3)}
                for i, score in self.index.top(query, max(1, min(int(k), 5)))]

    def read(self, page, offset=0, limit=1800):
        page, offset, limit = int(page), int(offset), int(limit)
        if page not in self.pages:
            raise KeyError(f"Unknown scenario page {page}")
        if offset < 0 or offset >= len(self.pages[page]) or not 1 <= limit <= 2000:
            raise ValueError("Invalid document offset or limit")
        return [self.row(page, offset, min(offset + limit, len(self.pages[page])), "read")]

    def valid_row(self, row):
        try:
            page, start, end = row["page"], row["start"], row["end"]
            return (type(page) is type(start) is type(end) is int and page in self.pages
                    and 0 <= start < end <= len(self.pages[page])
                    and row["source"] == self.source and row["source_sha256"] == self.sha256
                    and row["passage_id"] == f"scenario:p{page}:{start}-{end}"
                    and row["text"] == self.pages[page][start:end])
        except (KeyError, TypeError):
            return False

    def valid_reference(self, reference):
        if reference.get("source") != self.source:
            return False
        locator = reference.get("locator", "")
        match = re.fullmatch(r"PDF page (\d+) \(one-based\), chars (\d+)-(\d+) \(zero-based, end-exclusive\)", locator)
        if match:
            page, start, end = map(int, match.groups())
            return page in self.pages and 0 <= start < end <= len(self.pages[page])
        # Legacy outputs used page-only locators; P2 additionally requires exact selected passages.
        match = re.fullmatch(r"PDF page (\d+) \(one-based\)", locator)
        return bool(match and int(match[1]) in self.pages)


def reference(row):
    return {"source_type": "project_document", "source": row["source"],
            "locator": f"PDF page {row['page']} (one-based), chars {row['start']}-{row['end']} (zero-based, end-exclusive)"}


def evidence_error(store, finding, observed):
    seen = set()
    for selected in finding.get("documents", []):
        identifier = selected["passage_id"]
        if identifier in seen:
            return "duplicate_document_selection"
        seen.add(identifier)
        row = observed.get(identifier)
        if row is None:
            return "document_passage_not_delivered"
        if not store.documents.valid_row(row):
            return "document_source_or_range_mismatch"
        if selected["result"] in ("contradicts", "uncertain"):
            return "document_" + selected["result"] + "_requires_resolution"
    return None


def selected_evidence(store, checks, observed, recordable, blocked):
    """Keep the model's semantic decision distinct from code provenance checks and publication."""
    out = []
    for check, finding in checks.items():
        affected = finding.get("fields", []) if check == "conflict" else ["related_items" if check == "relation" else check]
        for selected in finding.get("documents", []):
            row = observed.get(selected["passage_id"])
            retained = (recordable and finding["result"] == "pass" and not set(affected).intersection(blocked)
                        and selected["result"] in ("supports", "context")
                        and row is not None and store.documents.valid_row(row)
                        and evidence_error(store, finding, observed) is None)
            out.append({"check": check, **copy.deepcopy(selected), "retained": bool(retained),
                        "evidence": copy.deepcopy(row)})
    return out


def selected_references(evidence):
    out = []
    for witness in evidence:
        if witness["retained"]:
            ref = reference(witness["evidence"])
            if ref not in out:
                out.append(ref)
    return out


def validate_outcome(store, item):
    errors = []
    rows = item.get("observed_documents", [])
    observed = {r["passage_id"]: r for r in rows}
    if len(observed) != len(rows):
        errors.append("p2: duplicate observed document passage")
    delivered = [row for event in item["history"] if event.get("action") in ("search_project_doc", "read_project_doc")
                 for row in event.get("tool_result", {}).get("results", [])]
    for row in rows:
        if not store.documents.valid_row(row) or row not in delivered:
            errors.append("p2: document passage differs from source or delivered tool result")
    blocked = []
    for check, finding in item["checks"].items():
        if finding["result"] not in ("pass", "not_applicable"):
            blocked += (finding["fields"] or ["statement"]) if check == "conflict" else ["related_items" if check == "relation" else check]
    expected = selected_evidence(store, item["checks"], observed, item["recordable"], blocked)
    if expected != item.get("document_evidence", []):
        errors.append("p2: document witness differs from selected, checked evidence")
    actual_refs = [r for r in item["fields"].get("reference", []) if r["source_type"] == "project_document"]
    if actual_refs != selected_references(expected):
        errors.append("p2: published document references do not match approved evidence")
    return errors
