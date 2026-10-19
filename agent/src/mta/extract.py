"""N2 extraction (LLM) and N3 normalisation (rules)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import config
from .llm import LLM
from .prompts import CONSOLIDATE_SYSTEM, EXTRACT_SYSTEM
from .store import tokenize

BASIS = ["explicit", "inferred", "ambiguous", "missing"]
STATUSES = ["open", "in_progress", "completed", "cancelled", "deferred", "unknown",
            "proposed", "adopted", "conditional", "rejected", "resolved"]
VALID_STATUS = {
    "actions": {"open", "in_progress", "completed", "cancelled", "deferred", "unknown"},
    "decisions": {"proposed", "adopted", "conditional", "rejected", "deferred", "unknown"},
    "problems": {"open", "resolved", "deferred", "unknown"},
}
DEFAULT_STATUS = {"actions": "open", "decisions": "adopted", "problems": "open"}
HEDGE = re.compile(r"\b(could|might|maybe|perhaps|propos\w*|suggest\w*|consider\w*|possibly|what if|ideas?)\b", re.I)
GENERIC_OWNER = re.compile(r"\b(we|someone|somebody|anyone|everyone|everybody|you guys|all of us|one of us)\b", re.I)

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "type": {"enum": ["actions", "decisions", "problems"]},
            "text": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "integer"}},
            "owner_ids": {"type": "array", "items": {"type": "string"}},
            "owner_cue": {"type": ["string", "null"]},
            "owner_basis": {"enum": BASIS},
            "deadline": {"type": ["string", "null"]},
            "deadline_cue": {"type": ["string", "null"]},
            "deadline_basis": {"enum": BASIS},
            "status": {"enum": STATUSES},
        },
        "required": ["type", "text", "evidence", "owner_ids", "owner_basis", "deadline", "deadline_basis", "status"],
    }}},
    "required": ["items"],
}


def chunks(n: int, size: int, overlap: int):
    start = 0
    while start < n:
        yield start, min(n, start + size)
        if start + size >= n:
            break
        start += size - overlap


def _clean(it: dict, ev: list[str]) -> dict | None:
    """The model does not always honour the schema enums: coerce what is safe, drop the rest."""
    if it.get("type") not in VALID_STATUS or not str(it.get("text", "")).strip():
        return None
    return {
        "type": it["type"], "text": str(it["text"]).strip(), "evidence_da_ids": ev,
        "owner_ids": [str(x) for x in (it.get("owner_ids") or [])],
        "owner_cue": it.get("owner_cue"), "deadline_cue": it.get("deadline_cue"),
        "owner_basis": it.get("owner_basis") if it.get("owner_basis") in BASIS else "missing",
        "deadline": it.get("deadline") if isinstance(it.get("deadline"), str) else None,
        "deadline_basis": it.get("deadline_basis") if it.get("deadline_basis") in BASIS else "missing",
        "status": it.get("status"),
    }


NL = chr(10)
ACTION_PASS_NOTE = ("List ONLY actions (follow-up work assigned to a role for after this meeting). You see the whole "
                    "meeting: assignments are usually settled late, often when the manager wraps up; use the roster IDs. "
                    "Do not list decisions or problems.")


def _lines(acts: list[dict], a: int, b: int) -> list[str]:
    return [f"[{i}] {acts[i]['attributes']['role']}({acts[i]['attributes']['participant']}): {acts[i]['text']}"
            for i in range(a, b)]


def _call(llm: LLM, roster_txt: str, note: str, acts: list[dict], a: int, b: int, keep: set[str]) -> list[dict]:
    user = f"Roster:{NL}{roster_txt}{NL}{NL}{note}{NL}{NL}Transcript:{NL}" + NL.join(_lines(acts, a, b))
    res = llm.forced_tool(EXTRACT_SYSTEM, user, "submit_candidates", CANDIDATE_SCHEMA)
    out = []
    for it in res.get("items", []):
        if it.get("type") not in keep:
            continue
        ev = [acts[i]["id"] for i in dict.fromkeys(it.get("evidence", [])) if isinstance(i, int) and a <= i < b]
        clean = _clean(it, ev) if ev else None  # candidates without real evidence are dropped
        if clean:
            out.append(clean)
    return out


def extract_candidates(llm: LLM, acts: list[dict], roster: list[dict], single_chunk: bool = False,
                       cache: Path | None = None) -> list[dict]:
    """Decisions/problems come from overlapping chunks (they are local); actions from one pass over the whole
    meeting (they are global: assignments refer back to the whole discussion). `single_chunk` is the one-shot
    baseline: one call, all types.  `cache` stores the raw candidates on disk so reruns skip the slow calls."""
    key = hashlib.sha256(json.dumps([EXTRACT_SYSTEM, ACTION_PASS_NOTE, llm.model, single_chunk, config.CHUNK,
                                     config.OVERLAP, [a["id"] for a in acts]]).encode()).hexdigest()[:16]
    if cache and cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            return saved["candidates"]
    roster_txt = NL.join(f"{r['participant_id']} = {r['role']}" for r in roster)
    if single_chunk:
        out = _call(llm, roster_txt, "List actions, decisions and problems.", acts, 0, len(acts), set(VALID_STATUS))
    else:
        out = []
        for a, b in chunks(len(acts), config.CHUNK, config.OVERLAP):
            out += _call(llm, roster_txt, "List only decisions and problems for this chunk (actions are collected "
                         "in a separate pass).", acts, a, b, {"decisions", "problems"})
        out += _call(llm, roster_txt, ACTION_PASS_NOTE, acts, 0, len(acts), {"actions"})
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "candidates": out}, ensure_ascii=False), encoding="utf-8")
    return out


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(tokenize(a)), set(tokenize(b))
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def normalize(cands: list[dict], valid_ids: set[str]) -> list[dict]:
    """Deterministic clean-up: valid participant IDs only, no generic owners, deadline wording kept, dedup."""
    everyone = len(valid_ids)
    items: list[dict] = []
    for c in cands:
        c = dict(c)
        if c["type"] == "actions":
            ids = [i for i in dict.fromkeys(c["owner_ids"]) if i in valid_ids]
            cue = c.get("owner_cue") or ""
            if (GENERIC_OWNER.search(cue) and len(ids) != 1) or (everyone > 1 and len(ids) >= everyone):
                # "we" or the whole roster: unspecified group, not a real joint assignment
                ids = []
                c["owner_basis"] = "ambiguous"
            if c["owner_basis"] in ("explicit", "inferred") and not ids:
                c["owner_basis"] = "missing"
            if c["owner_basis"] in ("ambiguous", "missing"):
                ids = []
            c["owner_ids"] = ids
            if c["deadline_basis"] in ("explicit", "inferred") and not (c.get("deadline") or "").strip():
                c["deadline_basis"] = "missing"
            if c["deadline_basis"] == "missing":
                c["deadline"] = None
        c["status"] = c["status"] if c["status"] in VALID_STATUS[c["type"]] else DEFAULT_STATUS[c["type"]]
        if c["type"] == "decisions" and c["status"] == "proposed" and not HEDGE.search(c["text"]):
            c["status"] = "adopted"  # SB gold: 81% of decisions are adopted; "proposed" only with hedged wording
        dup = next((x for x in items if x["type"] == c["type"] and _jaccard(x["text"], c["text"]) >= 0.6), None)
        if dup:
            dup["evidence_da_ids"] = list(dict.fromkeys(dup["evidence_da_ids"] + c["evidence_da_ids"]))
        else:
            items.append(c)
    return items


CONSOLIDATE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"type": {"enum": ["actions", "decisions", "problems"]},
                       "text": {"type": "string"},
                       "members": {"type": "array", "items": {"type": "integer"}}},
        "required": ["type", "text", "members"]}}},
    "required": ["items"],
}


def _merge_groups(groups: list[dict], cands: list[dict]) -> list[dict]:
    out = []
    for g in groups:
        members = [cands[i] for i in dict.fromkeys(g.get("members", [])) if isinstance(i, int) and 0 <= i < len(cands)]
        if g.get("type") not in VALID_STATUS or not members or not str(g.get("text", "")).strip():
            continue
        rank = {"explicit": 0, "inferred": 1, "ambiguous": 2, "missing": 3}
        best = min(members, key=lambda m: (m["type"] != g["type"], rank[m["owner_basis"]]))
        with_dl = [m for m in members if m.get("deadline")]
        owners = list(dict.fromkeys(o for m in members if m["type"] == g["type"] for o in m["owner_ids"]))
        merged = dict(best)
        merged.update(type=g["type"], text=str(g["text"]).strip(),
                      evidence_da_ids=list(dict.fromkeys(e for m in members for e in m["evidence_da_ids"])),
                      owner_ids=owners if best["owner_basis"] in ("explicit", "inferred") else [])
        if with_dl:
            merged.update(deadline=with_dl[0]["deadline"], deadline_basis=with_dl[0]["deadline_basis"],
                          deadline_cue=with_dl[0].get("deadline_cue"))
        out.append(merged)
    return out


def consolidate(llm: LLM, cands: list[dict], cache: Path | None = None) -> list[dict]:
    """One pass over all chunk candidates: select substantive items and merge duplicates.
    Cached on disk so that systems being compared (S1/S2) start from the same consolidated items."""
    if len(cands) <= 1:
        return cands
    listing = "\n".join(f"[{i}] ({c['type']}) {c['text']}"
                        + (f" | owner cue: {c['owner_cue']}" if c.get("owner_cue") else "")
                        + (f" | deadline: {c['deadline']}" if c.get("deadline") else "")
                        for i, c in enumerate(cands))
    key = hashlib.sha256((CONSOLIDATE_SYSTEM + llm.model + listing).encode()).hexdigest()[:16]
    if cache and cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            return saved["items"]
    for _ in range(3):  # the model sometimes returns an empty or unusable list; never fall back to the raw candidates
        res = llm.forced_tool(CONSOLIDATE_SYSTEM, "Candidates:" + chr(10) + listing, "submit_final_items", CONSOLIDATE_SCHEMA)
        out = _merge_groups([g for g in res.get("items", []) if g.get("type") in VALID_STATUS], cands)
        if out:
            if cache:
                cache.write_text(json.dumps({"key": key, "items": out}, ensure_ascii=False), encoding="utf-8")
            return out
    raise RuntimeError("consolidation produced no usable items after 3 attempts")
