"""Orchestration of N0-N10 for one meeting. Modes: b0 (one-shot baseline), s1 (no prior context), s2 (full)."""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .agent import MeetingBudget, build_reference, run_agent
from .extract import _strip_indexes, consolidate, extract_candidates, normalize
from .llm import LLM
from .observation import RunObservation, sha256
from .prompts import ABSTRACT_SYSTEM
from .render import render_report
from .store import Store, split_meeting_id
from .transcript_parser import TranscriptParseError, parse_transcript_file
from .trace import Trace
from .triage import flag_items
from .validate import check, repair

MODES = ("b0", "s1", "s2")
ABSTRACT_SCHEMA = {"type": "object", "properties": {"abstracts": {"type": "array", "items": {
    "type": "object", "properties": {"text": {"type": "string"}, "items": {"type": "array", "items": {"type": "integer"}}},
    "required": ["text", "items"]}}}, "required": ["abstracts"]}


def default_fields(store: Store, item: dict) -> dict:
    """Fields for an item without agent follow-up: what the extractor said, nothing inferred."""
    f = {"status": item["status"], "prior_context_needed": False, "related_items": []}
    if item["type"] == "actions":
        f.update(owner=item["owner_ids"] or None, owner_basis=item["owner_basis"],
                 deadline=item.get("deadline"), deadline_basis=item["deadline_basis"])
    f["reference"] = build_reference(store, item["evidence_da_ids"], f.get("owner"), False, [])
    return f


def make_record(meeting_id: str, items: list[dict], abstracts: list[dict]) -> list[dict]:
    out, n = [], 0
    for it in items:
        n += 1
        rec = {"id": f"{meeting_id}.sys.s.{n}", "text": it["text"], "type": it["type"], "reference": it["fields"]["reference"]}
        f = it["fields"]
        if it["type"] == "actions":
            rec.update(owner=f["owner"], owner_basis=f["owner_basis"], deadline=f["deadline"], deadline_basis=f["deadline_basis"])
        rec.update(status=f["status"], prior_context_needed=f["prior_context_needed"], related_items=f["related_items"])
        out.append(rec)
    for a in abstracts:
        n += 1
        out.append({"id": f"{meeting_id}.sys.s.{n}", "text": a["text"], "type": "abstract", "reference": a["reference"]})
    return out


def draft_abstracts(llm: LLM, items: list[dict]) -> list[dict]:
    """The abstract call only needs the item texts, so it can run while the agent is still checking fields."""
    if not items:
        return []
    listing = "\n".join(f"[{i}] ({it['type']}) {it['text']}" for i, it in enumerate(items))
    res = llm.forced_tool(ABSTRACT_SYSTEM, "Items of this meeting:\n" + listing, "submit_abstracts", ABSTRACT_SCHEMA,
                          effort=config.EFFORT_ABSTRACT, stage="abstract")
    out = []
    for a in (res.get("abstracts") or [])[:6]:
        if not isinstance(a, dict):
            continue  # skip malformed entries
        text = _strip_indexes(str(a.get("text", "")))  # the model sometimes writes its item citations ("[4],[5]") into the text
        text = re.sub(r"\s*,\s*([.;])", r"\1", text).strip().rstrip(",")
        if text:
            out.append({**a, "text": text})
    return out


def attach_abstract_refs(drafts: list[dict], items: list[dict]) -> list[dict]:
    """After the agent: each abstract cites the references of the items it summarises."""
    out = []
    for a in drafts:
        refs, seen = [], set()
        for i in a.get("items") or []:
            if isinstance(i, int) and 0 <= i < len(items):
                for r in items[i]["fields"]["reference"]:
                    k = json.dumps(r, sort_keys=True)
                    if k not in seen:
                        seen.add(k)
                        refs.append(r)
        out.append({"text": a["text"], "reference": refs})
    return out


def _safe_agent(llm: LLM, store: Store, item: dict, budget: MeetingBudget, trace: Trace, key: str) -> dict | None:
    """One item's agent failing (e.g. an API error after the SDK's retries) falls back for that item only."""
    try:
        return run_agent(llm, store, item, budget, trace, key)
    except Exception as e:  # noqa: BLE001 - logged and counted as a fallback in the report
        trace.log(item=key, step=None, flags=item["flags"], action=None, outcome="agent_error",
                  error=f"{type(e).__name__}: {str(e)[:300]}")
        return None


def _warm_store(store: Store) -> None:
    """Load every visible meeting and its search index up front, so parallel agent threads only read the store."""
    for mid in store.visible_ids():
        store.meeting(mid)
        store._index(mid)


def run_meeting(txt_path: Path, mode: str, llm: LLM, out_root: Path, cache_dir: Path | None = None) -> dict:
    """Run unchanged decisions/limits while recording independent provenance and stage snapshots."""
    assert mode in MODES
    effective_cache = cache_dir or config.RUNS / "_cache"
    observation = RunObservation(txt_path, mode, llm, out_root, effective_cache)
    before = llm.usage_snapshot()
    try:
        result = _run_meeting(txt_path, mode, llm, out_root, effective_cache, observation)
    except Exception as error:
        # Observe failures without adding retries or changing the existing exception policy.
        observation.finish({"status": "ERROR", "error_type": type(error).__name__},
                           LLM.usage_since(before, llm.usage_snapshot()))
        raise
    result.update(run_metadata=str(observation.path), stage_trace=str(observation.stage_path))
    observation.finish(result, LLM.usage_since(before, llm.usage_snapshot()))
    return result


def _run_meeting(txt_path: Path, mode: str, llm: LLM, out_root: Path, cache_dir: Path,
                 observation: RunObservation) -> dict:
    assert mode in MODES
    out_dir = out_root / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"mode": mode, "status": "COMPLETED"}
    # The LLM object is shared across a series and its counters only grow: report this meeting's share.
    usage0 = llm.usage_snapshot()
    t0 = time.time()
    seconds: dict[str, float] = {}

    # N0/N1: input and parser
    try:
        acts = parse_transcript_file(txt_path)
    except (TranscriptParseError, OSError) as e:
        return {**result, "status": "BLOCKED", "reason": f"parse_error: {e}"}
    meeting_id = acts[0]["id"].split(".")[0]
    observation.data["meeting_id"] = meeting_id
    observation.trace.context["meeting_id"] = meeting_id
    stages = observation.trace
    stages.log(stage="parse", event="completed", dialogue_acts=len(acts))
    series, order = split_meeting_id(meeting_id)
    result["meeting_id"] = meeting_id
    trace = Trace(out_dir / f"{meeting_id}.trace.jsonl")

    # N1b: participants
    store = Store(meeting_id, allow_prior=(mode == "s2"), run_dir=out_dir)
    observation.data["prior_context"] = [
        {"meeting_id": mid,
         "transcript_sha256": sha256(config.TRANSCRIPTS / f"{mid}.txt")
         if (config.TRANSCRIPTS / f"{mid}.txt").is_file() else None,
         "record_sha256": sha256(out_dir / f"{mid}.json") if (out_dir / f"{mid}.json").is_file() else None}
        for mid in store.visible_ids("prior")
    ]
    observation.save()
    if meeting_id not in store.participants:
        return {**result, "status": "BLOCKED", "reason": "participant_error: no roster for this meeting"}
    try:
        cur = store.meeting(meeting_id, path=txt_path)
    except KeyError as e:
        return {**result, "status": "BLOCKED", "reason": f"participant_error: speaker {e} has no roster entry"}

    # N2/N3: extract and normalise
    t = time.time()
    cands = extract_candidates(llm, cur.acts, store.roster(meeting_id), single_chunk=(mode == "b0"),
                               cache=cache_dir / f"extract_{mode == 'b0'}_{meeting_id}.json", trace=stages)
    seconds["extract"] = round(time.time() - t, 1)
    if mode != "b0":
        t = time.time()
        try:
            cands = consolidate(llm, cands, cache_dir / f"consolidate_{meeting_id}.json", trace=stages)
        except RuntimeError as e:
            return {**result, "status": "BLOCKED", "reason": f"consolidation_failed: {e}"}
        seconds["consolidate"] = round(time.time() - t, 1)
    items = normalize(cands, store.participant_ids())
    stages.log(stage="normalize", event="completed", items=items)

    # N4: triage (b0 has no follow-up)
    if mode == "b0":
        for it in items:
            it["flags"] = []
    else:
        flag_items(items, store)
    stages.log(stage="triage", event="completed", items=items)

    # N5: follow-up agent for flagged items only. Items are independent (they share only the meeting's lookup
    # budget, which is locked), so they run in parallel; the abstract call runs alongside them.
    budget = MeetingBudget()
    flagged = [n for n, it in enumerate(items) if it["flags"]]
    _warm_store(store)
    t = time.time()
    with ThreadPoolExecutor(max_workers=max(1, config.WORKERS)) as pool:
        drafts_job = pool.submit(draft_abstracts, llm, items)
        jobs = {n: pool.submit(_safe_agent, llm, store, items[n], budget, trace, f"{meeting_id}#{n}") for n in flagged}
        agent_fields = {n: j.result() for n, j in jobs.items()}
        drafts = drafts_job.result()
    seconds["agent+abstract"] = round(time.time() - t, 1)  # the two run in parallel
    agent_runs, fallbacks = len(flagged), sum(1 for f in agent_fields.values() if f is None)
    for n, it in enumerate(items):
        fields = agent_fields.get(n)
        if fields is not None and "STATUS_CHECK" not in it["flags"]:
            fields["status"] = it["status"]  # status was settled at extraction; the agent only revisits it on STATUS_CHECK
        it["fields"] = fields or default_fields(store, it)

    # N7: assemble
    record = make_record(meeting_id, items, attach_abstract_refs(drafts, items))
    stages.log(stage="assemble", event="completed", records=record,
               item_record_map={f"{meeting_id}#{n}": r["id"] for n, r in enumerate(record[:len(items)])})

    # N8/N9: validate, repair once, validate again
    errors = check(record, store)
    repairs = []
    if errors:
        repairs = repair(record, store)
        errors = check(record, store)
    stages.log(stage="validate", event="completed", errors=errors, repairs=repairs)
    if errors:
        (out_dir / f"{meeting_id}.blocked.json").write_text(
            json.dumps({"errors": errors, "draft": record}, indent=1, ensure_ascii=False), encoding="utf-8")
        return {**result, "status": "BLOCKED", "reason": "validation_failed", "errors": errors[:10]}

    # N10: write outputs (the JSON also feeds the next meeting's context store)
    (out_dir / f"{meeting_id}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    usage = LLM.usage_since(usage0, llm.usage_snapshot())
    seconds["total"] = round(time.time() - t0, 1)
    notes = {"items": sum(1 for r in record if r["type"] != "abstract"), "flagged": sum(1 for i in items if i["flags"]),
             "agent runs": agent_runs, "agent fallbacks": fallbacks, "lookups used": budget.used,
             "repairs": len(repairs), "input tokens": usage["in"], "output tokens": usage["out"],
             "llm calls": usage["calls"], "seconds": seconds["total"]}
    (out_dir / f"{meeting_id}_report.md").write_text(render_report(record, store, mode, notes), encoding="utf-8")
    # Per-stage cost (calls / tokens) and wall time; cache hits cost nothing and do not appear in `stages`.
    return {**result, **notes, "stages": usage["stages"], "stage_seconds": seconds,
            "output": str(out_dir / f"{meeting_id}.json")}
