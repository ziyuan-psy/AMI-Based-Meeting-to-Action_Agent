"""Select project-relevant, atomic claims before the single final count limit.

The model proposes selection and wording; provenance is checked here and semantic
support is checked independently by P2. No gold or reference outputs are read.
"""
from __future__ import annotations

import copy
import hashlib
import json

from jsonschema import Draft202012Validator

from . import config
from .extract import _strip_indexes
from .observation import write_json

SYSTEM = """Prepare substantive meeting-record items from the supplied candidate pool and actual dialogue.
Return every source index in a kept item's members or in excluded, with an explicit reason.
Preserve named follow-up assignments, including minutes, sharing promised slides and coordination work.
An Action and a product Decision may cite the same lines without being duplicates. Do not turn
capability, role descriptions or suggestions into commitments. Merge the SAME actor's SAME task only.
Decisions are independently testable product/project choices. Split separate price, sales target,
cost constraint, scope, feature inclusion and layout choices even if one source sentence contains them.
Do not split redundant restatements of the same choice. A member can support several atomic Decisions.
Do not treat introductions, icebreakers, drawing exercises or presentation order as project Decisions.
Problems must be actual project/team obstacles, including technical failures that materially disrupted
the meeting. A passing question, routine uncertainty or exercise difficulty alone is insufficient.
Do not erase a genuine problem merely because it was later resolved. Do not invent facts or dates.
Use only provided current dialogue IDs supporting each output. A candidate is a hypothesis, not proof.
Retain its core actor/task, conditions, polarity and epistemic status. Reword faithful facts, but
never infer a new assignment, upgrade a proposal to adoption, or add an unsupported timing restriction.
Use priority 3 for core assignments/settled choices/blockers, 2 for other substantive items, 1 for
lower-priority substantive details. List all justified items: code applies the final per-type limits
after deduplication and ranking, not by chopping the appended restoration tail.
"""

SCHEMA = {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "type": {"enum": ["actions", "decisions", "problems"]},
        "text": {"type": "string", "minLength": 1},
        "members": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 0}},
        "evidence_da_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "priority": {"type": "integer", "minimum": 1, "maximum": 3},
        "reason": {"type": "string", "minLength": 1}},
        "required": ["type", "text", "members", "evidence_da_ids", "priority", "reason"], "additionalProperties": False}},
    "excluded": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer", "minimum": 0}, "reason": {"type": "string", "minLength": 1},
        "category": {"enum": ["duplicate", "procedure", "not_project_relevant", "unsupported_candidate"]}},
        "required": ["index", "reason", "category"], "additionalProperties": False}}},
    "required": ["items", "excluded"], "additionalProperties": False}


def selection_error(result, pool, delivered):
    errors = list(Draft202012Validator(SCHEMA).iter_errors(result))
    if errors:
        return errors[0].message
    used, excluded = set(), []
    for item in result["items"]:
        members = set(item["members"])
        if not members <= set(range(len(pool))):
            return "member_outside_candidate_pool"
        if item["type"] == "actions" and not any(pool[i]["type"] == "actions" for i in members):
            return "new_action_without_action_source"
        allowed = {d for i in members for d in pool[i]["evidence_da_ids"]} & set(delivered)
        if not set(item["evidence_da_ids"]) <= allowed:
            return "evidence_outside_delivered_member_support"
        used |= members
    excluded = [row["index"] for row in result["excluded"]]
    if len(excluded) != len(set(excluded)) or used.intersection(excluded):
        return "contradictory_or_duplicate_exclusion"
    if used | set(excluded) != set(range(len(pool))):
        return "every_candidate_needs_an_explicit_disposition"
    return None


def ranked_cap(items, trace=None):
    """Rank the whole pool, including recovered tasks, before applying unchanged limits."""
    ranked = sorted(enumerate(items), key=lambda pair: (
        -pair[1].get("selection_priority", 2),
        -(pair[1]["type"] == "actions" and pair[1].get("owner_basis") == "explicit"),
        -(pair[1]["type"] == "actions" and pair[1].get("deadline_basis") == "explicit"), pair[0]))
    counts, kept, dropped = {}, [], []
    for _, item in ranked:
        typ = item["type"]
        counts[typ] = counts.get(typ, 0) + 1
        if counts[typ] <= config.MAX_ITEMS[typ]:
            kept.append(item)
        else:
            dropped.append({"item": item, "reason": "type_limit_after_selection", "rank": counts[typ],
                            "limit": config.MAX_ITEMS[typ]})
    if trace:
        trace.log(stage="select", event="cap", before=items, kept=kept, dropped=dropped,
                  limits=config.MAX_ITEMS, policy="project_priority_then_assignment_specificity_stable")
    return kept


def select_items(llm, pool, acts, cache=None, trace=None):
    if not pool:
        return []
    by_id = {a["id"]: a for a in acts}
    identifiers = list(dict.fromkeys(d for item in pool for d in item["evidence_da_ids"] if d in by_id))
    dialogue = [{"da_id": d, "text": by_id[d]["text"], **by_id[d]["attributes"]} for d in identifiers]
    candidates = [{"index": i, **{k: item.get(k) for k in
        ("type", "text", "evidence_da_ids", "owner_ids", "owner_basis", "deadline", "deadline_basis", "status")}}
        for i, item in enumerate(pool)]
    user = {"candidates": candidates, "observed_dialogue": dialogue}
    key = hashlib.sha256(json.dumps([SYSTEM, config.ITEM_POLICY_VERSION, llm.model, user], sort_keys=True).encode()).hexdigest()
    saved = json.loads(cache.read_text(encoding="utf-8")) if cache and cache.exists() else {}
    result = saved.get("response") if saved.get("key") == key else None
    source = "cache" if result is not None else "model"
    for attempt in range(2):
        if result is None:
            result = llm.forced_tool(SYSTEM, json.dumps(user, ensure_ascii=False), "select_record_items", SCHEMA,
                                     effort=config.EFFORT_CONSOLIDATE, stage="select")
        error = selection_error(result, pool, identifiers)
        if trace:
            trace.log(stage="select", event="attempt", attempt=attempt, source=source,
                      input=user, response=result, error=error)
        if not error:
            break
        user["correction"] = error
        result, source = None, "model"
    else:
        raise ValueError("item_selection_invalid: " + error)
    if cache and source == "model":
        write_json(cache, {"key": key, "response": result}, getattr(llm, "control", None))
    out = []
    for row in result["items"]:
        sources = [pool[i] for i in row["members"]]
        best = min(sources, key=lambda s: (s["type"] != row["type"], s.get("owner_basis") != "explicit"))
        item = copy.deepcopy(best)
        item.update(type=row["type"], text=_strip_indexes(row["text"]), evidence_da_ids=list(dict.fromkeys(row["evidence_da_ids"])),
                    source_item_indexes=row["members"], candidate_indexes=list(dict.fromkeys(
                        i for s in sources for i in s.get("candidate_indexes", []))),
                    selection_priority=row["priority"], selection_reason=row["reason"])
        out.append(item)
    if trace:
        trace.log(stage="select", event="completed", before=pool, items=out, excluded=result["excluded"])
    return ranked_cap(out, trace)
