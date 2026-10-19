"""Orchestration of N0-N10 for one meeting. Modes: b0 (one-shot baseline), s1 (no prior context), s2 (full)."""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .agent import MeetingBudget, build_reference, run_agent
from .extract import consolidate, extract_candidates, normalize
from .llm import LLM
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


def write_abstracts(llm: LLM, items: list[dict]) -> list[dict]:
    if not items:
        return []
    listing = "\n".join(f"[{i}] ({it['type']}) {it['text']}" for i, it in enumerate(items))
    res = llm.forced_tool(ABSTRACT_SYSTEM, "Items of this meeting:\n" + listing, "submit_abstracts", ABSTRACT_SCHEMA)
    out = []
    for a in (res.get("abstracts") or [])[:6]:
        if not isinstance(a, dict) or not str(a.get("text", "")).strip():
            continue  # the model does not always follow the schema; skip malformed entries
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


def run_meeting(txt_path: Path, mode: str, llm: LLM, out_root: Path, cache_dir: Path | None = None) -> dict:
    assert mode in MODES
    out_dir = out_root / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"mode": mode, "status": "COMPLETED"}

    # N0/N1: input and parser
    try:
        acts = parse_transcript_file(txt_path)
    except (TranscriptParseError, OSError) as e:
        return {**result, "status": "BLOCKED", "reason": f"parse_error: {e}"}
    meeting_id = acts[0]["id"].split(".")[0]
    series, order = split_meeting_id(meeting_id)
    result["meeting_id"] = meeting_id
    trace = Trace(out_dir / f"{meeting_id}.trace.jsonl")

    # N1b: participants
    store = Store(meeting_id, allow_prior=(mode == "s2"), run_dir=out_dir)
    if meeting_id not in store.participants:
        return {**result, "status": "BLOCKED", "reason": "participant_error: no roster for this meeting"}
    try:
        cur = store.meeting(meeting_id, path=txt_path)
    except KeyError as e:
        return {**result, "status": "BLOCKED", "reason": f"participant_error: speaker {e} has no roster entry"}

    # N2/N3: extract and normalise
    cands = extract_candidates(llm, cur.acts, store.roster(meeting_id), single_chunk=(mode == "b0"),
                               cache=(cache_dir or config.RUNS / "_cache") / f"extract_{mode == 'b0'}_{meeting_id}.json")
    if mode != "b0":
        try:
            cands = consolidate(llm, cands, (cache_dir or config.RUNS / "_cache") / f"consolidate_{meeting_id}.json")
        except RuntimeError as e:
            return {**result, "status": "BLOCKED", "reason": f"consolidation_failed: {e}"}
    items = normalize(cands, store.participant_ids())

    # N4: triage (b0 has no follow-up)
    if mode == "b0":
        for it in items:
            it["flags"] = []
    else:
        flag_items(items, store)

    # N5: follow-up agent for flagged items only
    budget = MeetingBudget()
    agent_runs = fallbacks = 0
    for n, it in enumerate(items):
        fields = None
        if it["flags"]:
            agent_runs += 1
            fields = run_agent(llm, store, it, budget, trace, f"{meeting_id}#{n}")
            if fields is None:
                fallbacks += 1
            elif "STATUS_CHECK" not in it["flags"]:
                fields["status"] = it["status"]  # status was settled at extraction; the agent only revisits it on STATUS_CHECK
        it["fields"] = fields or default_fields(store, it)

    # N7: assemble
    record = make_record(meeting_id, items, write_abstracts(llm, items))

    # N8/N9: validate, repair once, validate again
    errors = check(record, store)
    repairs = []
    if errors:
        repairs = repair(record, store)
        errors = check(record, store)
    if errors:
        (out_dir / f"{meeting_id}.blocked.json").write_text(
            json.dumps({"errors": errors, "draft": record}, indent=1, ensure_ascii=False), encoding="utf-8")
        return {**result, "status": "BLOCKED", "reason": "validation_failed", "errors": errors[:10]}

    # N10: write outputs (the JSON also feeds the next meeting's context store)
    (out_dir / f"{meeting_id}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    notes = {"items": sum(1 for r in record if r["type"] != "abstract"), "flagged": sum(1 for i in items if i["flags"]),
             "agent runs": agent_runs, "agent fallbacks": fallbacks, "lookups used": budget.used,
             "repairs": len(repairs), "input tokens": llm.in_tokens, "output tokens": llm.out_tokens}
    (out_dir / f"{meeting_id}_report.md").write_text(render_report(record, store, mode, notes), encoding="utf-8")
    return {**result, **notes, "output": str(out_dir / f"{meeting_id}.json")}
