"""Context store: enriched transcripts, BM25 search, scenario pages, prior system outputs.

Every read is bounded in code: only the current meeting and EARLIER meetings of
the SAME series are visible. Gold files are never read here.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .transcript_parser import parse_transcript_file

_TOKEN = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def history_tokens(text: str) -> list[str]:
    """Normalize spoken acronyms without changing extraction/deduplication's token rules."""
    text = re.sub(r"\b(?:[A-Za-z]_){2,}", lambda m: m.group().replace("_", ""), text)
    aliases = {"tv": ["television"], "lcd": ["display", "screen"], "ui": ["interface"], "id": ["industrial", "designer"]}
    out = []
    for token in tokenize(text):
        out.append(token)
        out.extend(aliases.get(token, []))
    return out


def split_meeting_id(meeting_id: str) -> tuple[str, int]:
    m = re.fullmatch(r"([A-Za-z]+[0-9]+)([a-d])", meeting_id)
    if not m:
        raise ValueError(f"bad meeting id: {meeting_id}")
    return m.group(1), "abcd".index(m.group(2)) + 1


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75, tokenizer=tokenize):
        self.k1, self.b = k1, b
        self.tokenize = tokenizer
        self.toks = [tokenizer(d) for d in docs]
        self.n = len(docs)
        self.avg = (sum(len(t) for t in self.toks) / self.n) if self.n else 0.0
        df: Counter = Counter()
        for t in self.toks:
            df.update(set(t))
        self.idf = {w: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(t) for t in self.toks]

    def score(self, query: str, i: int) -> float:
        tf, dl, s = self.tf[i], len(self.toks[i]), 0.0
        for w in set(self.tokenize(query)):
            if w in tf:
                f = tf[w]
                s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
        return s

    def top(self, query: str, k: int) -> list[tuple[int, float]]:
        scored = [(i, self.score(query, i)) for i in range(self.n)]
        scored = [x for x in scored if x[1] > 0]
        return sorted(scored, key=lambda x: -x[1])[:k]

    def self_score(self, query: str) -> float:
        """Upper-bound-ish score of the query against a doc made of itself (for normalisation)."""
        q = set(self.tokenize(query))
        return sum(self.idf.get(w, 0.0) * (self.k1 + 1) / (1 + self.k1 * (1 - self.b + self.b)) for w in q) or 1.0


@dataclass
class Meeting:
    meeting_id: str
    series: str
    order: int
    acts: list[dict]
    by_id: dict = field(default_factory=dict)

    def __post_init__(self):
        self.by_id = {a["id"]: i for i, a in enumerate(self.acts)}


class Store:
    """Visibility rules live here. `allow_prior=False` implements the S1 ablation."""

    def __init__(self, current: str, allow_prior: bool = True, run_dir: Path | None = None):
        self.current = current
        self.series, self.order = split_meeting_id(current)
        self.allow_prior = allow_prior
        self.run_dir = run_dir
        self.participants = json.loads(config.PARTICIPANTS.read_text(encoding="utf-8"))
        self._meetings: dict[str, Meeting] = {}
        self._bm25: dict[str, BM25] = {}
        self._scenario = self._load_scenario()
        self._history_lock = threading.RLock()
        self._prior_snapshots = {}

    # ---- meetings ---------------------------------------------------------
    def visible_ids(self, scope: str = "both") -> list[str]:
        ids = []
        if scope in ("current", "both"):
            ids.append(self.current)
        if scope in ("prior", "both") and self.allow_prior:
            ids += [f"{self.series}{'abcd'[o - 1]}" for o in range(1, self.order)]
        return ids

    def _check_visible(self, meeting_id: str) -> None:
        if meeting_id not in self.visible_ids():
            raise PermissionError(f"{meeting_id} is not readable from {self.current}")

    def meeting(self, meeting_id: str, path: Path | None = None) -> Meeting:
        self._check_visible(meeting_id)
        if meeting_id not in self._meetings:
            acts = parse_transcript_file(path or config.TRANSCRIPTS / f"{meeting_id}.txt")
            roster = self.participants[meeting_id]
            for a in acts:
                p = roster[a["speaker"]]
                a["attributes"] = {"participant": p["participant"], "role": p["role"]}
            series, order = split_meeting_id(meeting_id)
            self._meetings[meeting_id] = Meeting(meeting_id, series, order, acts)
        return self._meetings[meeting_id]

    def roster(self, meeting_id: str) -> list[dict]:
        self._check_visible(meeting_id)
        return [{"speaker": k, "participant_id": v["participant"], "role": v["role"]}
                for k, v in sorted(self.participants[meeting_id].items())]

    def participant_ids(self, meeting_id: str | None = None) -> set[str]:
        return {v["participant"] for v in self.participants[meeting_id or self.current].values()}

    def metadata_locator(self, meeting_id: str, participant_id: str) -> str | None:
        """Locator in the gold style: one dialogue act of that speaker + attribute names."""
        for v in self.participants[meeting_id].values():
            if v["participant"] == participant_id:
                return f"{v['first_da_id']} (attributes.participant and attributes.role)"
        return None

    def da_exists(self, meeting_id: str, da_id: str) -> bool:
        try:
            self._check_visible(meeting_id)
        except PermissionError:
            return False
        return da_id in self.meeting(meeting_id).by_id

    # ---- dialogue retrieval ----------------------------------------------
    def _index(self, meeting_id: str) -> BM25:
        if meeting_id not in self._bm25:
            self._bm25[meeting_id] = BM25([a["text"] for a in self.meeting(meeting_id).acts], tokenizer=history_tokens)
        return self._bm25[meeting_id]

    def search_dialogue(self, query: str, scope: str = "prior", k: int = 5) -> list[dict]:
        out = []
        for mid in self.visible_ids(scope):
            m = self.meeting(mid)
            for i, s in self._index(mid).top(query, k):
                a = m.acts[i]
                out.append({"da_id": a["id"], "meeting_id": mid, "participant": a["attributes"]["participant"],
                            "role": a["attributes"]["role"], "text": a["text"], "score": round(s, 2)})
        return sorted(out, key=lambda x: -x["score"])[:k]

    def read_dialogue(self, meeting_id: str, da_id: str, window: int = 8) -> list[dict]:
        self._check_visible(meeting_id)
        m = self.meeting(meeting_id)
        if da_id not in m.by_id:
            raise KeyError(f"{da_id} not found in {meeting_id}")
        window = max(1, min(int(window), 15))
        i = m.by_id[da_id]
        return [{"da_id": a["id"], "participant": a["attributes"]["participant"],
                 "role": a["attributes"]["role"], "text": a["text"]}
                for a in m.acts[max(0, i - window): i + window + 1]]

    # ---- scenario ----------------------------------------------------------
    def _load_scenario(self) -> tuple[list[tuple[int, str]], BM25]:
        raw = config.SCENARIO.read_text(encoding="utf-8")
        parts = re.split(r"=== PDF PAGE (\d+) ===", raw)
        pages = [(int(n), body.strip()) for n, body in zip(parts[1::2], parts[2::2])]
        return pages, BM25([b for _, b in pages])

    def search_project_doc(self, query: str, k: int = 3) -> list[dict]:
        pages, idx = self._scenario
        return [{"page": pages[i][0], "snippet": pages[i][1][:500], "score": round(s, 2)} for i, s in idx.top(query, k)]

    # ---- prior system outputs ----------------------------------------------
    def _prior_snapshot(self, mid):
        if mid not in self.visible_ids("prior") or self.run_dir is None:
            raise PermissionError("Prior snapshot outside this series/time window")
        with self._history_lock:
            if mid not in self._prior_snapshots:
                path = self.run_dir / f"{mid}.json"
                raw = path.read_bytes() if path.is_file() else None
                self._prior_snapshots[mid] = (json.loads(raw) if raw is not None else [],
                                               hashlib.sha256(raw).hexdigest() if raw is not None else None)
            return self._prior_snapshots[mid]

    def prior_items(self) -> list[dict]:
        if not self.allow_prior or self.run_dir is None:
            return []
        items = []
        for mid in self.visible_ids("prior"):
            for it in self._prior_snapshot(mid)[0]:
                if it["type"] != "abstract" and str(it.get("id", "")).split(".")[0] == mid:
                    items.append(it)
        return items

    def prior_index(self) -> tuple[list[dict], BM25 | None]:
        items = self.prior_items()
        return items, (BM25([i["text"] for i in items], tokenizer=history_tokens) if items else None)

    def _prior_metadata(self, item: dict) -> dict:
        mid = item["id"].split(".")[0]
        if mid not in self.visible_ids("prior") or self.run_dir is None:
            raise PermissionError("Only earlier recorded items of this series can be read")
        path = self.run_dir / f"{mid}.json"
        all_evidence = list(dict.fromkeys(r["locator"].split(" ")[0] for r in item.get("reference", []) if r["source_type"] == "dialogue"))
        old_series, old_order = split_meeting_id(mid)
        allowed = {f"{old_series}{c}" for c in "abcd"[:old_order]}
        evidence = [d for d in all_evidence if d.split(".")[0] in allowed]
        result = {"id": item["id"], "meeting_id": mid, "type": item["type"], "text": item["text"],
                  "owner": item.get("owner"), "owner_basis": item.get("owner_basis"),
                  "deadline": item.get("deadline"), "deadline_basis": item.get("deadline_basis"),
                  "status": item.get("status", "unknown"), "evidence": evidence,
                  "source_file": path.name, "source_sha256": self._prior_snapshot(mid)[1],
                  "review_state": "legacy_unchecked", "unresolved_fields": []}
        result["unreadable_reference_ids"] = [d for d in all_evidence if d not in evidence]
        sidecar = path.with_suffix(".p2.json")
        if sidecar.is_file():
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            for outcome in saved.get("items", []):
                if (outcome.get("record_id") == item["id"] and outcome.get("recordable")
                        and all(item.get(k) == v for k, v in outcome.get("fields", {}).items())):
                    result.update(review_state=outcome["state"], unresolved_fields=outcome["unresolved_fields"])
        return result

    def get_prior_items(self, query: str, item_type: str | None = None, k: int = 3) -> list[dict]:
        # Filter before ranking: unrelated high-scoring types must not hide the requested type.
        items = [i for i in self.prior_items() if not item_type or i["type"] == item_type]
        if not items:
            return []
        k = max(1, min(int(k), 8))
        idx = BM25([i["text"] for i in items], tokenizer=history_tokens)
        out = []
        for i, s in idx.top(query, max(1, min(int(k), 8))):
            it = items[i]
            out.append({**self._prior_metadata(it), "score": round(s, 2)})
        return out[:k]

    def read_prior_item(self, item_id: str, query: str = "", offset: int = 0, limit: int = 8, window: int = 1) -> list[dict]:
        """One bounded read of a retrieved record and actual cited dialogue; never edits history."""
        mid = item_id.split(".")[0]
        if mid not in self.visible_ids("prior"):
            raise PermissionError("Only same-series earlier records are readable")
        item = next((i for i in self.prior_items() if i["id"] == item_id), None)
        if item is None:
            raise KeyError(item_id)
        metadata = self._prior_metadata(item)
        readable = [d for d in metadata["evidence"] if self.da_exists(d.split(".")[0], d)]
        if query and readable:
            idx = BM25([self.meeting(d.split(".")[0]).acts[self.meeting(d.split(".")[0]).by_id[d]]["text"] for d in readable],
                       tokenizer=history_tokens)
            ranked = [readable[i] for i, _ in idx.top(query, len(readable))]
            readable = ranked + [d for d in readable if d not in ranked]
        offset, limit, window = max(0, int(offset)), max(1, min(int(limit), 12)), max(0, min(int(window), 2))
        selected = readable[offset:offset + limit]
        rows = [{"kind": "prior_item", **metadata, "selected_reference_ids": selected,
                 "next_offset": offset + limit if offset + limit < len(readable) else None,
                 "unreadable_reference_ids": metadata["unreadable_reference_ids"] + [d for d in metadata["evidence"] if d not in readable]}]
        seen = set()
        for d in selected:
            m = self.meeting(d.split(".")[0]); pos = m.by_id[d]
            for act in m.acts[max(0, pos-window):pos+window+1]:
                if act["id"] not in seen:
                    rows.append({"kind": "dialogue", "da_id": act["id"], "meeting_id": m.meeting_id,
                                 "text": act["text"], **act["attributes"], "supports_item_id": item_id,
                                 "is_reference": act["id"] in metadata["evidence"]})
                    seen.add(act["id"])
        return rows

    def prior_item_exists(self, item_id: str) -> bool:
        return any(i["id"] == item_id for i in self.prior_items())
