"""Orchestration of N0-N10 for one meeting. Modes: b0 (one-shot baseline), s1 (no prior context), s2 (full)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config
from .agent import MeetingBudget, build_reference, run_agent, _run_legacy_agent
from .control import Limits, RunControl, RunStopped, launch, stage_pool
from .extract import _strip_indexes, consolidate, extract_candidates, normalize
from .item_selection import select_items,ranked_cap
from .llm import LLM
from .observation import RunObservation, sha256, write_json
from .prompts import ABSTRACT_SYSTEM
from .render import render_report
from .store import Store, split_meeting_id
from .transcript_parser import TranscriptParseError, parse_transcript_file
from .trace import Trace
from .triage import flag_items
from .validate import check, repair
from .quality import effective as quality_policy, DEVELOPMENT

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


def _safe_agent(llm: LLM, store: Store, item: dict, budget: MeetingBudget, trace: Trace, key: str, progress=None,
                candidate=False) -> dict | None:
    """Keep model/control failures terminal; only explicit policy outcomes may remain pending."""
    try:
        if candidate:
            return _run_legacy_agent(llm, store, item, budget, trace, key, observed_only=True)
        return run_agent(llm, store, item, budget, trace, key, progress=progress)
    except RunStopped:
        raise
    except Exception as e:
        trace.log(item=key, step=None, flags=item["flags"], action=None, outcome="agent_error",
                  error_type=type(e).__name__)
        raise


def _warm_store(store: Store) -> None:
    """Load every visible meeting and its search index up front, so parallel agent threads only read the store."""
    for mid in store.visible_ids():
        store.meeting(mid)
        store._index(mid)


def run_meeting(txt_path: Path, mode: str, llm: LLM, out_root: Path, cache_dir: Path | None = None) -> dict:
    """One supervised meeting: shared admission, bounded waits, durable checkpoints and publication."""
    assert mode in MODES
    try:
        policy = quality_policy(mode)
    except ValueError:
        return {"mode": mode, "meeting_id": txt_path.stem, "status": "FAILED", "failed_stage": "configuration",
                "reason": "invalid_quality_profile", "artifacts_saved": False}
    try:
        limits = Limits.configured()
    except ValueError:
        return {"mode": mode, "meeting_id": txt_path.stem, "status": "FAILED", "failed_stage": "configuration",
                "reason": "invalid_limits", "artifacts_saved": False}
    effective_cache = cache_dir or config.RUNS / "_cache"
    if policy['name'] != DEVELOPMENT:
        effective_cache = effective_cache / policy['name']
    try:
        observation = RunObservation(txt_path, mode, llm, out_root, effective_cache)
    except Exception as error:
        return {"mode": mode, "meeting_id": txt_path.stem, "status": "FAILED", "failed_stage": "initialize",
                "reason": "artifact_initialization_failed", "error_type": type(error).__name__, "artifacts_saved": False}
    before = llm.usage_snapshot()
    control = None
    try:
        control = RunControl(limits, trace=observation.trace)
        observation.control = control
        scoped = llm.scoped(control)
        future = launch(lambda: _run_meeting(txt_path, mode, scoped, out_root, effective_cache, observation))
        result = control.wait(future)
        if result["status"] == "COMPLETED":
            record, report = result.pop("_record"), result.pop("_report")
            observation.enter("publish")
            p2, clarifications = result.pop("_p2"), result.pop("_clarifications")
            result["output"] = observation.publish(result["meeting_id"], record, report, {"p2": p2, "clarifications": clarifications})
            result["output_sha256"] = sha256(Path(result["output"]))
            result["report_sha256"] = sha256(Path(result["output"]).with_name(f"{result['meeting_id']}_report.md"))
            for name in ("p2", "clarifications"):
                artifact = Path(result["output"]).with_suffix(f".{name}.json")
                result[name + "_artifact"] = str(artifact)
                result[name + "_sha256"] = sha256(artifact)
    except RunStopped as error:
        result = {"mode": mode, "meeting_id": observation.data["meeting_id"],
                  "status": error.failure["status"], "reason": error.failure["code"],
                  "failed_stage": error.failure["stage"], "failure": error.failure}
    except KeyboardInterrupt:
        failure = (control.stop("cancelled", "CANCELLED").failure if control else
                   {"code": "cancelled", "status": "CANCELLED", "stage": "configuration"})
        result = {"mode": mode, "meeting_id": observation.data["meeting_id"], "status": failure["status"],
                  "reason": failure["code"], "failed_stage": failure["stage"], "failure": failure}
    except Exception as error:
        failure = (control.stop("stage_failed", "FAILED", error_type=type(error).__name__,
                   os_error=getattr(error,'errno',None),windows_error=getattr(error,'winerror',None),
                   io_operation=getattr(error,'mta_io_operation',None)).failure if control else
                   {"code": "invalid_limits", "status": "FAILED", "stage": "configuration", "error_type": type(error).__name__})
        result = {"mode": mode, "meeting_id": observation.data["meeting_id"], "status": failure["status"],
                  "reason": failure["code"], "failed_stage": failure["stage"], "failure": failure}
    if control is not None:
        control.seal()
        result["runtime_control"] = control.snapshot()
    result.update(run_metadata=str(observation.path), stage_trace=str(observation.stage_path))
    usage = control.usage_snapshot() if control is not None else LLM.usage_since(before, llm.usage_snapshot())
    try:
        observation.finish(result, usage)
    except Exception as error:
        observation.agent_trace.close()
        if result["status"] == "COMPLETED":
            result.update(status="FAILED", failed_stage="finalize", reason="artifact_persistence_failed")
        result.update(artifacts_saved=False, persistence_error_type=type(error).__name__)
        # A failure to archive traces must not leave a successful commit marker behind.
        # If persistence itself is unavailable, the returned result is still authoritative.
        observation.data.update(status=result["status"], result=result)
        try:
            observation.trace.finish(stage="run", event="finalization_failed", error_type=type(error).__name__)
            observation.save(final=True)
        except Exception:
            pass
    return result


def saved_run_complete(record_path: Path) -> bool:
    """A prior JSON alone is insufficient after a failed rerun or interrupted publication."""
    try:
        metadata = json.loads(record_path.with_suffix(".run.json").read_text(encoding="utf-8"))
        report = record_path.with_name(f"{record_path.stem}_report.md")
        policy = quality_policy(metadata['mode'])
        if metadata.get('quality_policy') != policy:
            return False
        enabled = policy['p2_enabled']
        result = metadata["result"]
        return (metadata["status"] == "COMPLETED" and result.get("output_sha256") == sha256(record_path)
                and result.get("report_sha256") == sha256(report) and result.get("p2_enabled", False) == enabled
                and (not enabled or metadata["mode"] != "s2" or metadata["config"].get("LINK_POLICY_VERSION", 0) == config.LINK_POLICY_VERSION)
                and (not enabled or metadata["config"].get("DOCUMENT_POLICY_VERSION", 0) == config.DOCUMENT_POLICY_VERSION)
                and (not enabled or metadata['config'].get('P2_PROTOCOL_VERSION',0) == config.P2_PROTOCOL_VERSION)
                and (policy['candidate'] or metadata['mode'] == 'b0' or all(metadata['config'].get(k) == getattr(config,k)
                     for k in ('ITEM_SELECTION_ENABLED','ITEM_POLICY_VERSION')))
                and (not enabled or metadata.get("context_sha256", {}).get(config.SCENARIO.name) == sha256(config.SCENARIO))
                and (not enabled or all(metadata["config"].get(k) == getattr(config, k)
                                        for k in ("SCENARIO_PASSAGE_CHARS", "SCENARIO_PASSAGE_OVERLAP")))
                and (not enabled or all(result.get(name + "_sha256") == sha256(record_path.with_suffix(f".{name}.json"))
                                        for name in ("p2", "clarifications"))))
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
    control = observation.control

    # N0/N1: input and parser
    observation.enter("parse")
    try:
        acts = parse_transcript_file(txt_path)
    except (TranscriptParseError, OSError) as e:
        return {**result, "status": "BLOCKED", "reason": "parse_error", "failed_stage": "parse", "error_type": type(e).__name__}
    meeting_id = acts[0]["id"].split(".")[0]
    observation.update(meeting_id=meeting_id)
    with control.guard():
        observation.trace.context["meeting_id"] = meeting_id
    stages = observation.trace
    policy = observation.data['quality_policy']
    result.update(quality_profile=policy['name'], quality_policy=policy)
    stages.log(stage='run', event='quality_policy', policy=policy)
    stages.log(stage="parse", event="completed", dialogue_acts=len(acts))
    series, order = split_meeting_id(meeting_id)
    result["meeting_id"] = meeting_id
    trace = observation.agent_trace
    observation.checkpoint("parsed", acts)

    # N1b: participants
    observation.enter("participants")
    store = Store(meeting_id, allow_prior=(mode == "s2"), run_dir=out_dir)
    if mode == 's2':
        for prior_id in store.visible_ids('prior'):
            prior_path = out_dir / (prior_id + '.json')
            if not prior_path.exists():
                continue
            meta_path = prior_path.with_suffix('.run.json')
            prior_meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
            recorded_policy = prior_meta.get('quality_policy')
            if ((policy['candidate'] or recorded_policy is not None)
                    and (recorded_policy != policy or not saved_run_complete(prior_path))):
                return {**result, 'status': 'BLOCKED', 'reason': 'prior_quality_profile_mismatch',
                        'failed_stage': 'participants', 'prior_meeting': prior_id}
    prior_context = [
        {"meeting_id": mid,
         "transcript_sha256": sha256(config.TRANSCRIPTS / f"{mid}.txt")
         if (config.TRANSCRIPTS / f"{mid}.txt").is_file() else None,
         "record_sha256": sha256(out_dir / f"{mid}.json") if (out_dir / f"{mid}.json").is_file() else None}
        for mid in store.visible_ids("prior")
    ]
    observation.update(prior_context=prior_context)
    if meeting_id not in store.participants:
        return {**result, "status": "BLOCKED", "reason": "participant_error", "failed_stage": "participants"}
    try:
        cur = store.meeting(meeting_id, path=txt_path)
    except KeyError as e:
        return {**result, "status": "BLOCKED", "reason": "participant_error", "failed_stage": "participants"}

    # N2/N3: extract and normalise
    observation.enter("extract")
    t = time.time()
    if policy['candidate']:
        from . import recall_candidate
    extract_step = recall_candidate.extract_candidates if policy['candidate'] else extract_candidates
    cands = extract_step(llm, cur.acts, store.roster(meeting_id), single_chunk=(mode == "b0"),
                               cache=cache_dir / f"extract_{mode == 'b0'}_{meeting_id}.json", trace=stages)
    seconds["extract"] = round(time.time() - t, 1)
    observation.checkpoint("extracted", cands)
    if mode != "b0":
        observation.enter("consolidate")
        t = time.time()
        try:
            if policy['candidate']:
                cands = recall_candidate.consolidate(llm, cands, cache_dir / f"consolidate_{meeting_id}.json", trace=stages)
            else:
                cands = consolidate(llm, cands, cache_dir / f"consolidate_{meeting_id}.json", trace=stages,
                                    enable_decision_pass=policy['decision_second_pass'])
        except RuntimeError as e:
            return {**result, "status": "BLOCKED", "reason": "consolidation_failed", "failed_stage": "consolidate"}
        seconds["consolidate"] = round(time.time() - t, 1)
        observation.checkpoint("consolidated", cands)
        if policy['selection_enabled']:
            observation.enter("select")
            t = time.time()
            cands = select_items(llm, cands, cur.acts, cache=cache_dir / f"select_{meeting_id}.json", trace=stages,
                                 defer_action_limit=config.P2_ENABLED)
            seconds["select"] = round(time.time() - t, 1)
            observation.checkpoint("selected", cands)
        else:
            stages.log(stage='select', event='skipped', reason='quality_profile',
                       quality_profile=policy['name'], items=cands)
    observation.enter("normalize")
    items = normalize(cands, store.participant_ids())
    stages.log(stage="normalize", event="completed", items=items)
    observation.checkpoint("normalized", items)

    # N4: triage (b0 has no follow-up)
    observation.enter("triage")
    if mode == "b0":
        for it in items:
            it["flags"] = []
    else:
        flag_items(items, store)
        if policy['candidate']:
            for item in items:
                if item.get('restored') or item.get('restored_problem'):
                    item['flags'].append('RESTORED_SOURCE_REVIEW')
    stages.log(stage="triage", event="completed", items=items)
    observation.checkpoint("triaged", items)

    # N5: P2 checks every non-baseline item; the legacy ablation keeps flag-only follow-up.
    p2_enabled = policy['p2_enabled']
    budget = MeetingBudget()
    flagged = [n for n, it in enumerate(items) if it["flags"]]
    selected = list(range(len(items))) if p2_enabled else flagged
    for n, item in enumerate(items):
        item["run_id"] = observation.data["run_id"]
        item["peer_items"] = [{"item_key": f"{meeting_id}#{j}", "type": peer["type"], "text": peer["text"]}
                              for j, peer in enumerate(items) if j < n]
    observation.enter("context")
    _warm_store(store)
    observation.enter("agent+abstract")
    t = time.time()
    from .p2_admission import P2Admission
    admission = P2Admission(control,selected,config.P2_RESERVED_COMPLETION_CALLS) if p2_enabled else None

    def agent_item(n):
        progress = (lambda state: observation.checkpoint(f"p2_item_{n}", state)) if p2_enabled else None
        # Attempts, including transport retries, still pass through the original controller.
        item_llm = llm.parent.scoped(admission.view(n)) if admission else llm
        try:
            if policy['candidate']:
                fields = _safe_agent(item_llm, store, items[n], budget, trace, f"{meeting_id}#{n}", progress, candidate=True)
            else:
                fields = _safe_agent(item_llm, store, items[n], budget, trace, f"{meeting_id}#{n}", progress)
        finally:
            if admission:
                admission.done(n)
        observation.checkpoint(f"agent_item_{n}", {"item": n, "fields": fields})
        return fields

    with stage_pool(max(1, config.WORKERS), control) as pool:
        jobs = {n: pool.submit(agent_item, n) for n in selected}
        agent_fields = {n: control.wait(j) for n, j in jobs.items()}
    agent_runs, fallbacks = len(selected), sum(1 for f in agent_fields.values() if f is None)
    if admission:
        observation.update(p2_admission=admission.snapshot())
    original_items = items
    retained, p2_items = [], []
    for n, it in enumerate(items):
        fields = agent_fields.get(n)
        if p2_enabled:
            if fields is None:
                raise ValueError("P2 requires an explicit checked or pending outcome")
            fields.update(record_id=None, item_type=it["type"])
            # A bounded P2 deletion is published only after a new assessment of that text.
            it = {**it, "text": fields["item_text"]}
            p2_items.append(fields)
            it["fields"] = fields["fields"]
            if not fields["recordable"]:
                continue
        else:
            if fields is not None and "STATUS_CHECK" not in it["flags"]:
                fields["status"] = it["status"]
            it["fields"] = fields or default_fields(store, it)
        it["item_key"] = f"{meeting_id}#{n}"
        retained.append(it)
    items = retained
    omitted=[]
    if p2_enabled and policy['selection_enabled']:
        # A bounded 2x Action review pool lets owner/core checks happen before the final 5-item cap.
        # Preserve valid omitted outcomes separately; never relabel them as failed evidence checks.
        observation.checkpoint('verified_before_cap',items)
        items=ranked_cap(items,stages,stage='publication_select')
        keys={it['item_key'] for it in items}
        omitted=[o for o in p2_items if o['recordable'] and o['item_key'] not in keys]
        observation.checkpoint('verified_omitted',{'reason':'final_type_limit','outcomes':omitted})
        observation.checkpoint('publication_selected',items)
        p2_items=[o for o in p2_items if not o['recordable'] or o['item_key'] in keys]
    p2_payload = {"format_version": 1, "run_id": observation.data["run_id"], "meeting_id": meeting_id,
                  "enabled": p2_enabled, "items": p2_items}
    # Summaries are generated only after quarantining unsupported statements.
    drafts = draft_abstracts(llm, items)
    observation.checkpoint("abstracts", drafts)
    seconds["agent+abstract"] = round(time.time() - t, 1)

    # N7: assemble
    observation.enter("assemble")
    record = make_record(meeting_id, items, attach_abstract_refs(drafts, items))
    mapped = {it["item_key"]: rec["id"] for it, rec in zip(items, record)}
    requests = []
    for outcome in p2_items:
        outcome["record_id"] = mapped.get(outcome["item_key"])
        for request in outcome["clarifications"]:
            request["record_id"] = outcome["record_id"]
            requests.append(request)
    clarifications = {"format_version": 1, "run_id": observation.data["run_id"], "meeting_id": meeting_id,
                      "requests": requests}
    observation.checkpoint("p2", p2_payload)
    observation.checkpoint("clarifications", clarifications)
    stages.log(stage="assemble", event="completed", records=record,
               item_record_map=mapped)
    observation.checkpoint("assembled", record)

    # N8/N9: validate, repair once, validate again
    observation.enter("validate")
    errors = check(record, store)
    repairs = []
    if errors:
        repairs = repair(record, store)
        errors = check(record, store)
    from .p2 import validate_outputs
    errors += validate_outputs(p2_payload, clarifications, record, store)
    stages.log(stage="validate", event="completed", errors=errors, repairs=repairs)
    observation.checkpoint("validated", {"draft": record, "errors": errors, "repairs": repairs})
    if errors:
        write_json(out_dir / f"{meeting_id}.blocked.json", {"errors": errors, "draft": record}, control)
        return {**result, "status": "BLOCKED", "reason": "validation_failed", "failed_stage": "validate", "errors": errors[:10]}

    # N10: write outputs (the JSON also feeds the next meeting's context store)
    observation.enter("render")
    usage = LLM.usage_since(usage0, llm.usage_snapshot())
    seconds["total"] = round(time.time() - t0, 1)
    notes = {"items": sum(1 for r in record if r["type"] != "abstract"), "flagged": sum(1 for i in items if i["flags"]),
             "agent runs": agent_runs, "agent fallbacks": fallbacks, "lookups used": budget.used,
             "repairs": len(repairs), "input tokens": usage["in"], "output tokens": usage["out"],
             "llm calls": usage["calls"], "seconds": seconds["total"]}
    notes.update({"p2 accepted": sum(o["state"] == "accepted" for o in p2_items),
                  "p2 pending": sum(o["state"] == "pending" for o in p2_items),
                  "quarantined": sum(not o['recordable'] for o in p2_items) if p2_enabled else len(original_items)-len(items),
                  "verified omitted by limit":len(omitted),"clarification requests": len(requests)})
    if policy['candidate']:
        notes['review coverage'] = 'Targeted follow-up only; full evidence/conflict checks were not run.'
    report = render_report(record, store, mode, notes, p2_payload)
    observation.checkpoint("report", {"markdown": report})
    # Per-stage cost (calls / tokens) and wall time; cache hits cost nothing and do not appear in `stages`.
    return {**result, **notes, "stages": usage["stages"], "stage_seconds": seconds,
            "p2_enabled": p2_enabled, "_record": record, "_report": report,
            "_p2": p2_payload, "_clarifications": clarifications}
