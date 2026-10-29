"""N2 extraction (LLM) and N3 normalisation (rules)."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
CHUNK_NOTE = "List only decisions and problems for this chunk (actions are collected in a separate pass)."
B0_NOTE = "List actions, decisions and problems."
ACTION_PASS_NOTE = (
    "List ONLY actions: follow-up work that a person takes on for the period AFTER this meeting. You see the whole "
    "meeting. Go through the roster one role at a time and ask: what did this person agree, or get asked, to do "
    "before the next meeting (prepare, research, design, draw, write, send, report, finish)? Assignments are usually "
    "settled late, when the manager wraps up and says what each person does next: read the last part of the meeting "
    "line by line, but also catch tasks taken on earlier. A task given to everyone (e.g. filling in a questionnaire) "
    "is still an action. Joint tasks list every assigned person. Use the roster IDs. Do not list decisions, problems, "
    "or work done during the meeting itself.")


def _lines(acts: list[dict], a: int, b: int) -> list[str]:
    return [f"[{i}] {acts[i]['attributes']['role']}({acts[i]['attributes']['participant']}): {acts[i]['text']}"
            for i in range(a, b)]


def _call(llm: LLM, roster_txt: str, note: str, acts: list[dict], a: int, b: int, keep: set[str],
          retry_empty: bool = False) -> list[dict]:
    """One extraction call. `retry_empty`: for whole-meeting passes, where zero usable items almost always means a
    bad response (e.g. an empty list or no valid evidence), ask again up to EMPTY_RETRIES times."""
    user = f"Roster:{NL}{roster_txt}{NL}{NL}{note}{NL}{NL}Transcript:{NL}" + NL.join(_lines(acts, a, b))
    out: list[dict] = []
    for _ in range(1 + (config.EMPTY_RETRIES if retry_empty else 0)):
        res = llm.forced_tool(EXTRACT_SYSTEM, user, "submit_candidates", CANDIDATE_SCHEMA, effort=config.EFFORT_EXTRACT,
                              stage="extract")
        out = []
        for it in res.get("items") or []:
            if not isinstance(it, dict) or it.get("type") not in keep:
                continue
            ev = [acts[i]["id"] for i in dict.fromkeys(it.get("evidence") or []) if isinstance(i, int) and a <= i < b]
            clean = _clean(it, ev) if ev else None  # candidates without real evidence are dropped
            if clean:
                out.append(clean)
        if out:
            break
    return out


def _key(*parts) -> str:
    p = list(parts)
    if config.EFFORT_EXTRACT:
        p.append(config.EFFORT_EXTRACT)
    return hashlib.sha256(json.dumps(p).encode()).hexdigest()[:16]


def _overlap(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / min(len(sa), len(sb)) if sa and sb else 0.0


def union_actions(samples: list[list[dict]]) -> list[dict]:
    """Actions from several samples of the action pass: the first sample in order, then whatever later samples add.
    A candidate is a repeat when it cites mostly the same lines or says nearly the same thing."""
    kept: list[dict] = []
    for sample in samples:
        for c in sample:
            if any(_overlap(c["evidence_da_ids"], k["evidence_da_ids"]) >= 0.5 or _jaccard(c["text"], k["text"]) >= 0.5
                   for k in kept):
                continue
            kept.append(c)
    return kept


def extract_candidates(llm: LLM, acts: list[dict], roster: list[dict], single_chunk: bool = False,
                       cache: Path | None = None) -> list[dict]:
    """Decisions/problems come from overlapping chunks (they are local); actions from passes over the whole
    meeting (they are global: assignments refer back to the whole discussion), sampled config.ACTION_SAMPLES times
    and united to raise recall. `single_chunk` is the one-shot baseline: one call, all types.

    `cache` keeps each part under its own key, so changing the action pass does not redo the chunk pass and adding
    action samples only runs the missing ones. Empty results are never cached."""
    ids = [a["id"] for a in acts]
    roster_txt = NL.join(f"{r['participant_id']} = {r['role']}" for r in roster)
    saved = {}
    if cache and cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("version") != 2:  # the old single-key format cannot be split into parts
            saved = {}
    keys = {"single": _key(EXTRACT_SYSTEM, B0_NOTE, llm.model, ids),
            "chunk_pass": _key(EXTRACT_SYSTEM, CHUNK_NOTE, llm.model, config.CHUNK, config.OVERLAP, ids),
            "action_pass": _key(EXTRACT_SYSTEM, ACTION_PASS_NOTE, llm.model, ids)}

    def cached(part: str):
        p = saved.get(part) or {}
        return p if p.get("key") == keys[part] else None

    jobs = []  # (part, note, a, b, keep, retry_empty)
    if single_chunk:
        if not (cached("single") or {}).get("candidates"):
            jobs.append(("single", B0_NOTE, 0, len(acts), set(VALID_STATUS), True))
    else:
        if cached("chunk_pass") is None:  # an empty chunk pass is a real answer (nothing local to list); keep it
            jobs += [("chunk_pass", CHUNK_NOTE, a, b, {"decisions", "problems"}, False)
                     for a, b in chunks(len(acts), config.CHUNK, config.OVERLAP)]
        have = [s for s in (cached("action_pass") or {}).get("samples", []) if s]
        jobs += [("action_pass", ACTION_PASS_NOTE, 0, len(acts), {"actions"}, True)] * max(0, config.ACTION_SAMPLES - len(have))
    # The calls are independent: run them together; results keep the job order (chunks in transcript order).
    with ThreadPoolExecutor(max_workers=max(1, config.WORKERS)) as pool:
        parts = list(pool.map(lambda j: _call(llm, roster_txt, j[1], acts, j[2], j[3], j[4], retry_empty=j[5]), jobs))
    new = {"single": [], "chunk_pass": [], "action_pass": []}
    for (part, *_), res in zip(jobs, parts):
        if part == "action_pass":
            new[part].append(res)
        else:
            new[part] += res

    out_parts = {}
    for part in ("single", "chunk_pass"):
        ran = any(j[0] == part for j in jobs)
        got = new[part] if ran else (cached(part) or {}).get("candidates", [])
        out_parts[part] = got
        # Whole-meeting results are never cached empty (an empty b0 answer is almost always a bad response).
        if ran and (got or part == "chunk_pass"):
            saved[part] = {"key": keys[part], "candidates": got}
    samples = [s for s in ((cached("action_pass") or {}).get("samples", []) + new["action_pass"]) if s]
    if samples:
        saved["action_pass"] = {"key": keys["action_pass"], "samples": samples}
    if cache and jobs and any(saved.get(p, {}).get("candidates") or saved.get(p, {}).get("samples") for p in keys):
        saved["version"] = 2
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    if single_chunk:
        return out_parts["single"]
    return out_parts["chunk_pass"] + union_actions(samples[:config.ACTION_SAMPLES])


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


_INDEX_LIST = re.compile(r"\s*\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]")


def _strip_indexes(text: str) -> str:
    """The model sometimes copies candidate indexes ("... [93, 89, 90]") into the item text."""
    return re.sub(r"\s{2,}", " ", _INDEX_LIST.sub("", text)).strip()


def _merge_groups(groups: list[dict], cands: list[dict]) -> list[dict]:
    out = []
    for g in groups:
        members = [cands[i] for i in dict.fromkeys(g.get("members", [])) if isinstance(i, int) and 0 <= i < len(cands)]
        text = _strip_indexes(str(g.get("text", "")))
        if g.get("type") not in VALID_STATUS or not members or not text:
            continue
        typ = g["type"]
        member_types = Counter(m["type"] for m in members)
        if typ == "actions" and "actions" not in member_types:
            # Actions only come from the action pass. A group without any action candidate was relabelled by the
            # model (typically a decision rewritten as "X will ..."): keep the members' own type.
            typ = member_types.most_common(1)[0][0]
        rank = {"explicit": 0, "inferred": 1, "ambiguous": 2, "missing": 3}
        best = min(members, key=lambda m: (m["type"] != typ, rank[m["owner_basis"]]))
        with_dl = [m for m in members if m.get("deadline")]
        owners = list(dict.fromkeys(o for m in members if m["type"] == typ for o in m["owner_ids"]))
        merged = dict(best)
        merged.update(type=typ, text=text,
                      evidence_da_ids=list(dict.fromkeys(e for m in members for e in m["evidence_da_ids"])),
                      owner_ids=owners if best["owner_basis"] in ("explicit", "inferred") else [])
        if with_dl:
            merged.update(deadline=with_dl[0]["deadline"], deadline_basis=with_dl[0]["deadline_basis"],
                          deadline_cue=with_dl[0].get("deadline_cue"))
        out.append(merged)
    return _cap(out)


def _cap(items: list[dict]) -> list[dict]:
    """Hard cap per type. The prompt asks for the most important items first, so the tail is what gets cut."""
    kept, seen = [], Counter()
    for it in items:
        seen[it["type"]] += 1
        if seen[it["type"]] <= config.MAX_ITEMS[it["type"]]:
            kept.append(it)
    return kept


def restore_actions(items: list[dict], cands: list[dict]) -> list[dict]:
    """Put back action candidates that consolidation dropped, then cap again.

    The action pass reads the whole meeting and already lists few candidates, but consolidation sometimes drops all
    of a meeting's actions. A candidate counts as absorbed when all of its evidence lines are inside one kept item
    (a merged item's evidence is the union of its members' evidence); the others are appended in candidate order.
    """
    kept_evidence = [set(it["evidence_da_ids"]) for it in items]
    restored = []
    for c in cands:
        if c["type"] != "actions":
            continue
        ev = set(c["evidence_da_ids"])
        if any(ev <= k for k in kept_evidence):
            continue
        restored.append({**c, "text": _strip_indexes(c["text"]), "restored": True})
        kept_evidence.append(ev)  # a later candidate on the same lines is a duplicate of this one
    return _cap(items + restored)


def consolidate(llm: LLM, cands: list[dict], cache: Path | None = None) -> list[dict]:
    """One pass over all chunk candidates: select substantive items and merge duplicates.
    Cached on disk so that systems being compared (S1/S2) start from the same consolidated items."""
    if len(cands) <= 1:
        return cands
    listing = "\n".join(f"[{i}] ({c['type']}) {c['text']}"
                        + (f" | owner cue: {c['owner_cue']}" if c.get("owner_cue") else "")
                        + (f" | deadline: {c['deadline']}" if c.get("deadline") else "")
                        for i, c in enumerate(cands))
    key = hashlib.sha256((CONSOLIDATE_SYSTEM + llm.model + listing + (config.EFFORT_CONSOLIDATE or "")
                          + json.dumps(config.MAX_ITEMS, sort_keys=True)).encode()).hexdigest()[:16]
    # The cache holds each sampled consolidation (the model's items); voting and restore_actions run after it on
    # both paths, so changing those rules, or adding samples, needs no repeated consolidation call.
    runs: list[list[dict]] = []
    if cache and cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            runs = saved.get("runs") or ([saved["items"]] if saved.get("items") else [])
    missing = max(0, config.CONSOLIDATE_SAMPLES - len(runs))
    if missing:
        def one_run(_):
            for _attempt in range(3):  # the model sometimes returns an empty or unusable list
                res = llm.forced_tool(CONSOLIDATE_SYSTEM, "Candidates:" + chr(10) + listing, "submit_final_items",
                                      CONSOLIDATE_SCHEMA, effort=config.EFFORT_CONSOLIDATE, stage="consolidate")
                out = _merge_groups([g for g in res.get("items", []) if g.get("type") in VALID_STATUS], cands)
                if out:
                    return out
            return None
        with ThreadPoolExecutor(max_workers=max(1, min(missing, config.WORKERS))) as pool:
            runs += [r for r in pool.map(one_run, range(missing)) if r]
        if cache and runs:
            cache.write_text(json.dumps({"key": key, "runs": runs}, ensure_ascii=False), encoding="utf-8")
    if not runs:  # never fall back to the raw candidates
        raise RuntimeError("consolidation produced no usable items after 3 attempts")
    runs = runs[:config.CONSOLIDATE_SAMPLES]
    return restore_actions(vote_items(runs) if len(runs) > 1 else runs[0], cands)


def vote_items(runs: list[list[dict]]) -> list[dict]:
    """Keep the items that a majority of the sampled consolidations agree on.

    Two items from different runs are the same item when they have the same type and cite mostly the same lines
    (overlap >= 0.5 of the smaller evidence set). Each cluster is represented by its item from the earliest run;
    clusters keep the importance order the runs gave them (mean position within their type)."""
    clusters: list[dict] = []
    for r, items in enumerate(runs):
        pos = Counter()
        for it in items:
            p = pos[it["type"]]
            pos[it["type"]] += 1
            options = [(c, _overlap(c["rep"]["evidence_da_ids"], it["evidence_da_ids"])) for c in clusters
                       if r not in c["runs"] and c["rep"]["type"] == it["type"]]
            best = max(options, key=lambda o: o[1], default=(None, 0.0))
            if best[0] is not None and best[1] >= 0.5:
                best[0]["runs"].add(r)
                best[0]["pos"].append(p)
            else:
                clusters.append({"rep": it, "runs": {r}, "pos": [p], "order": len(clusters)})
    need = len(runs) // 2 + 1
    kept = [c for c in clusters if len(c["runs"]) >= need]
    kept.sort(key=lambda c: (sum(c["pos"]) / len(c["pos"]), c["order"]))
    return [c["rep"] for c in kept]
