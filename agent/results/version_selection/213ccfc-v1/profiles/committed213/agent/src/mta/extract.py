"""N2 extraction (LLM) and N3 normalisation (rules)."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from . import config
from .control import stage_pool
from .llm import LLM
from .observation import write_json
from .prompts import CONSOLIDATE_SYSTEM, DECISION_CONSOLIDATE_SYSTEM, EXTRACT_SYSTEM
from .store import tokenize
from .trace import StageTrace, StageTraceView


def _audit(trace, stage: str, event: str, **data):
    if trace is not None:
        trace.log(stage=stage, event=event, **data)

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
        "owner_ids": list(dict.fromkeys(str(x).strip() for x in (it.get("owner_ids") or []) if x is not None and str(x).strip())),
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
          retry_empty: bool = False, trace=None, job: dict | None = None) -> list[dict]:
    """One extraction call. `retry_empty`: for whole-meeting passes, where zero usable items almost always means a
    bad response (e.g. an empty list or no valid evidence), ask again up to EMPTY_RETRIES times."""
    user = f"Roster:{NL}{roster_txt}{NL}{NL}{note}{NL}{NL}Transcript:{NL}" + NL.join(_lines(acts, a, b))
    out: list[dict] = []
    for attempt in range(1 + (config.EMPTY_RETRIES if retry_empty else 0)):
        res = llm.forced_tool(EXTRACT_SYSTEM, user, "submit_candidates", CANDIDATE_SCHEMA, effort=config.EFFORT_EXTRACT,
                              stage="extract")
        out, rejected, accepted = [], [], []
        for index, it in enumerate(res.get("items") or []):
            if not isinstance(it, dict) or it.get("type") not in keep:
                rejected.append({"raw_index": index, "reason": "invalid_or_unrequested_type"})
                continue
            ev = [acts[i]["id"] for i in dict.fromkeys(it.get("evidence") or []) if isinstance(i, int) and a <= i < b]
            clean = _clean(it, ev) if ev else None  # candidates without real evidence are dropped
            if clean:
                accepted.append({"raw_index": index, "candidate_index": len(out)})
                out.append(clean)
            else:
                rejected.append({"raw_index": index, "reason": "no_valid_evidence" if not ev else "empty_text"})
        _audit(trace, "extract", "attempt", job=job, attempt=attempt, raw_items=res.get("items"),
               candidates=out, accepted=accepted, rejected=rejected)
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
                       cache: Path | None = None, trace=None) -> list[dict]:
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

    cached_action_samples = [s for s in (cached("action_pass") or {}).get("samples", []) if s]
    jobs = []  # (part, note, a, b, keep, retry_empty)
    if single_chunk:
        if not (cached("single") or {}).get("candidates"):
            jobs.append(("single", B0_NOTE, 0, len(acts), set(VALID_STATUS), True))
    else:
        if cached("chunk_pass") is None:  # an empty chunk pass is a real answer (nothing local to list); keep it
            jobs += [("chunk_pass", CHUNK_NOTE, a, b, {"decisions", "problems"}, False)
                     for a, b in chunks(len(acts), config.CHUNK, config.OVERLAP)]
        have = cached_action_samples
        jobs += [("action_pass", ACTION_PASS_NOTE, 0, len(acts), {"actions"}, True)] * max(0, config.ACTION_SAMPLES - len(have))
    # The calls are independent: run them together; results keep the job order (chunks in transcript order).
    with stage_pool(max(1, config.WORKERS), getattr(llm, "control", None)) as pool:
        parts = list(pool.map(lambda ij: _call(
            llm, roster_txt, ij[1][1], acts, ij[1][2], ij[1][3], ij[1][4], retry_empty=ij[1][5], trace=trace,
            job={"index": ij[0], "part": ij[1][0], "start": ij[1][2], "end": ij[1][3]}), enumerate(jobs)))
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
        if part == ("single" if single_chunk else "chunk_pass"):
            _audit(trace, "extract", "part", part=part, cache_key=keys[part],
                   source="model" if ran else "cache", candidates=got)
    samples = [s for s in ((cached("action_pass") or {}).get("samples", []) + new["action_pass"]) if s]
    if samples:
        saved["action_pass"] = {"key": keys["action_pass"], "samples": samples}
    if cache and jobs and any(saved.get(p, {}).get("candidates") or saved.get(p, {}).get("samples") for p in keys):
        saved["version"] = 2
        write_json(cache, saved, getattr(llm, "control", None))
    if single_chunk:
        out = out_parts["single"]
    else:
        selected = samples[:config.ACTION_SAMPLES]
        actions = union_actions(selected)
        _audit(trace, "extract", "action_union", cache_key=keys["action_pass"], samples=selected, kept=actions,
               cached_samples=min(len(cached_action_samples), config.ACTION_SAMPLES))
        out = out_parts["chunk_pass"] + actions
    _audit(trace, "extract", "completed", candidates=out,
           counts=dict(Counter(c["type"] for c in out)))
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


_INDEX_LIST = re.compile(r"\s*\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]")


def _strip_indexes(text: str) -> str:
    """The model sometimes copies candidate indexes ("... [93, 89, 90]") into the item text."""
    return re.sub(r"\s{2,}", " ", _INDEX_LIST.sub("", text)).strip()


def _merge_groups(groups: list[dict], cands: list[dict], trace=None, apply_cap=True) -> list[dict]:
    out, lineage, rejected = [], [], []
    if not isinstance(groups, list):
        _audit(trace, "consolidate", "merged", groups=groups, items=[], lineage=[],
               rejected=[{"group_index": None, "reason": "groups_must_be_array"}])
        return []
    for group_index, g in enumerate(groups):
        if not isinstance(g, dict):
            rejected.append({"group_index": group_index, "reason": "group_must_be_object"})
            continue
        indexes = g.get("members")
        if not isinstance(indexes, list):
            rejected.append({"group_index": group_index, "reason": "members_must_be_array"})
            continue
        invalid = [i for i, value in enumerate(indexes) if type(value) is not int]
        if invalid:
            # Do not hash objects, coerce participant IDs, or keep only part of an ambiguous group.
            rejected.append({"group_index": group_index, "reason": "members_must_be_candidate_indexes",
                             "invalid_positions": invalid})
            continue
        member_indexes = list(dict.fromkeys(i for i in indexes if 0 <= i < len(cands)))
        members = [cands[i] for i in member_indexes]
        typ = g.get("type")
        text = _strip_indexes(g["text"]) if isinstance(g.get("text"), str) else ""
        if not isinstance(typ, str) or typ not in VALID_STATUS or not members or not text:
            rejected.append({"group_index": group_index, "reason": "invalid_type_members_or_text"})
            continue
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
                      owner_ids=owners if best["owner_basis"] in ("explicit", "inferred") else [],
                      candidate_indexes=member_indexes)
        if with_dl:
            merged.update(deadline=with_dl[0]["deadline"], deadline_basis=with_dl[0]["deadline_basis"],
                          deadline_cue=with_dl[0].get("deadline_cue"))
        lineage.append({"group_index": group_index, "candidate_indexes": member_indexes, "item_index": len(out),
                        "requested_type": g["type"], "actual_type": typ})
        out.append(merged)
    _audit(trace, "consolidate", "merged", groups=groups, items=out, lineage=lineage, rejected=rejected)
    return _cap(out, trace, phase="sample") if apply_cap else out


def _cap(items: list[dict], trace=None, phase: str = "final") -> list[dict]:
    """Hard cap per type. The prompt asks for the most important items first, so the tail is what gets cut."""
    kept, dropped, seen = [], [], Counter()
    for it in items:
        seen[it["type"]] += 1
        if seen[it["type"]] <= config.MAX_ITEMS[it["type"]]:
            kept.append(it)
        else:
            dropped.append({"item": it, "reason": "type_limit", "rank": seen[it["type"]],
                            "limit": config.MAX_ITEMS[it["type"]]})
    _audit(trace, "consolidate", "cap", phase=phase, limits=config.MAX_ITEMS, before=items, kept=kept, dropped=dropped)
    return kept


def restore_actions(items: list[dict], cands: list[dict], trace=None, apply_cap=True) -> list[dict]:
    """Put back action candidates that consolidation dropped, then cap again.

    The action pass reads the whole meeting and already lists few candidates, but consolidation sometimes drops all
    of a meeting's actions. A candidate counts as absorbed when all of its evidence lines are inside one kept item
    (a merged item's evidence is the union of its members' evidence); the others are appended in candidate order.
    """
    # A Decision on the same lines does not discharge a person's task. In particular,
    # the Decision pass can replace that Decision after the first restoration.
    actions = [it for it in items if it["type"] == "actions"]
    restored = []
    for c in cands:
        if c["type"] != "actions":
            continue
        ev = set(c["evidence_da_ids"])
        if any(ev <= set(k["evidence_da_ids"]) and
               (not c.get("owner_ids") or not k.get("owner_ids") or
                set(c["owner_ids"]).intersection(k["owner_ids"])) for k in actions):
            continue
        restored.append({**c, "text": _strip_indexes(c["text"]), "restored": True})
        actions.append(c)
    _audit(trace, "consolidate", "restored_actions", before=items, restored=restored)
    return _cap(items + restored, trace, phase="after_restore") if apply_cap else items + restored


def consolidate(llm: LLM, cands: list[dict], cache: Path | None = None, trace=None,
                decision_pass=False, enable_decision_pass=False) -> list[dict]:
    """One pass over all chunk candidates: select substantive items and merge duplicates.
    Cached on disk so that systems being compared (S1/S2) start from the same consolidated items."""
    if len(cands) <= 1:
        _audit(trace, "consolidate", "skipped", reason="at_most_one_candidate", items=cands)
        return cands
    prompt = DECISION_CONSOLIDATE_SYSTEM if decision_pass else CONSOLIDATE_SYSTEM
    listing = "\n".join(f"[{i}] ({c['type']}) {c['text']}"
                        + (f" | owner cue: {c['owner_cue']}" if c.get("owner_cue") else "")
                        + (f" | deadline: {c['deadline']}" if c.get("deadline") else "")
                        for i, c in enumerate(cands))
    key = hashlib.sha256(("candidate-lineage-v1" + prompt + llm.model + listing + (config.EFFORT_CONSOLIDATE or "")
                          + json.dumps(config.MAX_ITEMS, sort_keys=True)).encode()).hexdigest()[:16]
    # The cache holds each sampled consolidation (the model's items); voting and restore_actions run after it on
    # both paths, so changing those rules, or adding samples, needs no repeated consolidation call.
    runs: list[list[dict]] = []
    run_traces = []
    if cache and cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            runs = saved.get("runs") or ([saved["items"]] if saved.get("items") else [])
            run_traces = saved.get("run_traces", [])
    run_traces += [None] * (len(runs) - len(run_traces))
    # Raw groups are authoritative. Old cache entries already had sample caps applied;
    # rebuild from the captured group response when available before final selection.
    if config.ITEM_SELECTION_ENABLED:
        usable_runs, usable_traces = [], []
        for index, details in enumerate(run_traces[:len(runs)]):
            groups = [e["groups"] for attempt in (details or []) for e in attempt.get("events", [])
                      if e.get("event") == "raw_groups"]
            if groups:
                runs[index] = _merge_groups(groups[-1], cands, trace, apply_cap=False)
            if runs[index]:
                usable_runs.append(runs[index]); usable_traces.append(details)
            else:
                _audit(trace, "consolidate", "sample_rejected", sample=index, source="cache",
                       reason="no_usable_cached_groups", recorded_attempts=details)
        runs, run_traces = usable_runs, usable_traces
    for index, items in enumerate(runs[:config.CONSOLIDATE_SAMPLES]):
        _audit(trace, "consolidate", "sample", sample=index, source="cache", cache_key=key, items=items,
               recorded_attempts=run_traces[index],
               detail_available=run_traces[index] is not None)
    missing = max(0, config.CONSOLIDATE_SAMPLES - len(runs))
    if missing:
        first_sample = len(runs)

        def one_run(index):
            attempts = []
            user = "Candidates:" + chr(10) + listing
            for _attempt in range(3):  # the model sometimes returns an empty or unusable list
                res = llm.forced_tool(prompt, user, "submit_final_items",
                                      CONSOLIDATE_SCHEMA, effort=config.EFFORT_CONSOLIDATE, stage="decision_consolidate" if decision_pass else "consolidate")
                detail = StageTrace()
                groups = res.get("items") if isinstance(res, dict) else None
                detail.log(stage="consolidate", event="raw_groups", groups=groups, raw_response=res)
                out = _merge_groups(groups, cands, detail,
                                    apply_cap=not config.ITEM_SELECTION_ENABLED)
                attempts.append({"attempt": _attempt, "events": detail.events})
                _audit(trace, "consolidate", "attempt", sample=index, attempt=_attempt, cache_key=key,
                       source="model", events=detail.events, items=out)
                if out:
                    return out, attempts
                reasons = sorted({r["reason"] for r in detail.events[-1].get("rejected", [])}) or ["no_usable_groups"]
                user = ("Candidates:" + chr(10) + listing + "\nCorrection: previous response had no usable groups ("
                        + ", ".join(reasons) + "). Return the advertised items array. Each group needs a valid type, "
                        "supported text, and members as candidate integer indexes from 0 to " + str(len(cands)-1)
                        + ". Do not use participant IDs, objects, strings or booleans as members. Do not guess an index.")
            return None, attempts
        with stage_pool(max(1, min(missing, config.WORKERS)), getattr(llm, "control", None)) as pool:
            for items, details in pool.map(one_run, range(first_sample, first_sample + missing)):
                if items:
                    runs.append(items)
                    run_traces.append(details)
        if cache and runs:
            write_json(cache, {"key": key, "runs": runs, "run_traces": run_traces}, getattr(llm, "control", None))
    if not runs:  # never fall back to the raw candidates
        raise RuntimeError("consolidation produced no usable items after 3 attempts")
    runs = runs[:config.CONSOLIDATE_SAMPLES]
    _audit(trace, "consolidate", "samples", runs=runs, cache_key=key)
    if len(runs) > 1:
        voted = vote_items(runs, trace, refine_decisions=decision_pass)
    else:
        voted = runs[0]
        _audit(trace, "consolidate", "vote_skipped", reason="one_usable_sample", kept=voted)
    out = restore_actions(voted, cands, trace, apply_cap=not config.ITEM_SELECTION_ENABLED)
    if enable_decision_pass and not decision_pass and any(c["type"] == "decisions" for c in cands):
        before = [i for i in out if i["type"] == "decisions"]
        decision_cache = cache.with_name(cache.stem + ".decisions.json") if cache else None
        selected = consolidate(llm, cands, decision_cache, StageTraceView(trace, "decision_consolidate") if trace else None,
                               decision_pass=True)
        decisions = [i for i in selected if i["type"] == "decisions"]
        out = [i for i in out if i["type"] != "decisions"] + decisions
        _audit(trace, "decision_consolidate", "replaced", before=before, items=decisions)
        out = restore_actions(out, cands, trace, apply_cap=not config.ITEM_SELECTION_ENABLED)
    _audit(trace, "consolidate", "completed", items=out)
    return out


def vote_items(runs: list[list[dict]], trace=None, refine_decisions=False) -> list[dict]:
    """Keep the items that a majority of the sampled consolidations agree on.

    Actions/problems from different runs are the same item when they have the same type and cite mostly the same lines
    (overlap >= 0.5 of the smaller evidence set). Each cluster is represented by its item from the earliest run;
    clusters keep the importance order the runs gave them (mean position within their type).
    Decisions use candidate lineage so a broad merged item cannot consume another choice's vote."""
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
    decisions = [(r, p, it) for r, run in enumerate(runs)
                 for p, it in enumerate(i for i in run if i["type"] == "decisions")]
    if refine_decisions and decisions and all(it.get("candidate_indexes") for _, _, it in decisions):
        clusters = [c for c in clusters if c["rep"]["type"] != "decisions"]
        clusters += _decision_clusters(runs, decisions, trace)
    elif refine_decisions and decisions:
        _audit(trace, "consolidate", "decision_vote_fallback", reason="candidate_lineage_unavailable")
    kept = [c for c in clusters if len(c["runs"]) >= need]
    kept.sort(key=lambda c: (sum(c["pos"]) / len(c["pos"]), c["order"]))
    _audit(trace, "consolidate", "vote", samples=len(runs), threshold=need,
           clusters=[{"item": c["rep"], "runs": sorted(c["runs"]), "votes": len(c["runs"]),
                      "kept": len(c["runs"]) >= need} for c in clusters], kept=[c["rep"] for c in kept])
    return [c["rep"] for c in kept]


def _decision_clusters(runs, decisions, trace=None):
    """Vote for the finest observed choices, allowing a broad group to support each of its parts.

    A vote means that sample included the representative's source candidates. It is not a fresh evidence
    verification. Exact candidate containment avoids confusing different choices citing the same dialogue.
    Never manufacture a new statement: representatives are actual sampled consolidation outputs.
    """
    representatives, suppressed = [], []
    for r, p, item in sorted(decisions, key=lambda v: (len(set(v[2]["candidate_indexes"])), v[0], v[1])):
        members = set(item["candidate_indexes"])
        narrower = [c for c in representatives if c["members"] <= members]
        if narrower and set().union(*(c["members"] for c in narrower)) == members:
            suppressed.append({"sample": r, "position": p, "item": item,
                               "covered_by": [sorted(c["members"]) for c in narrower]})
            continue
        representatives.append({"rep": item, "members": members, "source_sample": r, "source_position": p})
    clusters, details = [], []
    need = len(runs) // 2 + 1
    for order, representative in enumerate(representatives):
        supports = []
        for r, run in enumerate(runs):
            options = [(p, it) for p, it in enumerate(i for i in run if i["type"] == "decisions")
                       if representative["members"] <= set(it["candidate_indexes"])]
            if options:
                p, item = min(options, key=lambda v: (len(set(v[1]["candidate_indexes"])), v[0]))
                supports.append({"sample": r, "position": p, "candidate_indexes": item["candidate_indexes"],
                                 "broad": set(item["candidate_indexes"]) != representative["members"]})
        cluster = {"rep": representative["rep"], "runs": {s["sample"] for s in supports},
                   "pos": [s["position"] for s in supports], "order": order}
        clusters.append(cluster)
        details.append({"item": representative["rep"], "source_sample": representative["source_sample"],
                        "candidate_indexes": sorted(representative["members"]), "supports": supports,
                        "votes": len(supports), "kept": len(supports) >= need})
    _audit(trace, "consolidate", "decision_vote", policy="candidate_containment_finest_observed",
           threshold=need, clusters=details, suppressed_broad_groups=suppressed)
    return clusters
