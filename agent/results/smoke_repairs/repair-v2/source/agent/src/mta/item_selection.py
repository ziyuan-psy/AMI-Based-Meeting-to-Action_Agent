"""Select project-relevant, atomic claims before the single final count limit.

The model proposes selection and wording; provenance is checked here and semantic
support is checked independently by P2. No gold or reference outputs are read.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from jsonschema import Draft202012Validator

from . import config
from .extract import _strip_indexes
from .observation import write_json

SYSTEM = """EDIT the candidate pool into project minutes. Do not merely echo the input list.
First exclude procedure/non-project material, then merge duplicate tasks and split compound Decisions.
Use actual dialogue, not the candidate's label, as the evidence. Return only the advertised tool fields.
The input candidates are keyed C0, C1, etc. Return ONE review under EACH identical key.
Each review has keep (boolean), claims (array of {text,priority}), and reason. Excluded reviews have
keep=false and claims=[]. A duplicate can name duplicate_of (another input key kept in this response).
Keep source keys stable even when ranking; they identify source claims, not output positions or dialogue lines.
Preserve named follow-up assignments, including minutes, sharing promised slides and coordination work.
For Actions write a task verb phrase, with the assignee kept separately in the source owner_ids.
Do not repeat an unverified candidate assignee as the subject of the task text. Preserve recipients,
targets, conditions and timing. The separate owner check must be able to correct attribution.
An Action and a product Decision may cite the same lines without being duplicates. Do not turn
capability, role descriptions or suggestions into commitments. Merge the SAME actor's SAME task only.
Decisions are independently testable product/project choices. Split separate price, sales target,
cost constraint, scope, feature inclusion and layout choices even if one source sentence contains them.
Do not split redundant restatements of the same choice. A member can support several atomic Decisions.
Do not treat introductions, icebreakers, drawing exercises or presentation order as project Decisions.
Problems must be actual project/team obstacles, including technical failures that materially disrupted
the meeting. A passing question, routine uncertainty or exercise difficulty alone is insufficient.
Do not erase a genuine problem merely because it was later resolved. Do not invent facts or dates.
Code binds each keyed review to that source's supplied dialogue. A candidate is a hypothesis, not proof.
Retain its core actor/task, conditions, polarity and epistemic status. Reword faithful facts, but
never infer a new assignment, upgrade a proposal to adoption, or add an unsupported timing restriction.
Use priority 3 for specific role deliverables, settled project choices and blockers, 2 for substantive
coordination, 1 for lower-priority details. Rank distinct deliverables ahead of requests for information,
routine communications and hypothetical future phase duties. List each type in decreasing project impact,
not input order. Merge actor aliases with identical participant IDs for the same task.
List all justified items: code applies the final per-type limits
after deduplication and ranking, not by chopping the appended restoration tail.
"""

SCHEMA = {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "type": {"enum": ["actions", "decisions", "problems"]},
        "text": {"type": "string", "minLength": 1},
        "members": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 0}},
        "priority": {"type": "integer", "minimum": 1, "maximum": 3},
        "reason": {"type": "string", "minLength": 1}},
        "required": ["type", "text", "members", "priority", "reason"], "additionalProperties": False}},
    "excluded": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer", "minimum": 0}, "reason": {"type": "string", "minLength": 1},
        "category": {"enum": ["duplicate", "procedure", "not_project_relevant", "unsupported_candidate"]}},
        "required": ["index", "reason", "category"], "additionalProperties": False}}},
    "required": ["items", "excluded"], "additionalProperties": False}


def normalize_submission(result, pool):
    """Fill only a provably empty exclusion list; never infer a missing disposition."""
    result = copy.deepcopy(result)
    if isinstance(result,dict) and isinstance(result.get('items'),list) and 'excluded' not in result:
        members = {i for row in result['items'] if isinstance(row,dict)
                   for i in row.get('members',[]) if isinstance(i,int)}
        if members == set(range(len(pool))):
            result['excluded'] = []
    return result


def review_schema(pool):
    claim = {"type":"object","properties":{"text":{"type":"string","minLength":1},
             "priority":{"type":"integer","minimum":1,"maximum":3}},
             "required":["text","priority"],"additionalProperties":False}
    return {"type":"object","properties":{f"C{i}":{"type":"object","properties":{
        "keep":{"type":"boolean"},"claims":{"type":"array","maxItems":8,"items":claim},
        "reason":{"type":"string","minLength":1},
        "duplicate_of":{"enum":[f"C{j}" for j in range(len(pool))]}},
        "required":["keep","claims","reason"],"additionalProperties":False} for i in range(len(pool))},
        "required":[f"C{i}" for i in range(len(pool))],"additionalProperties":False}


def procedure_only(text):
    return (bool(re.search(r'\b(introductions?|get acquainted|ice[- ]?breaker)\b',text,re.I)) and
            not re.search(r'\b(product|launch|client|customer|design|scope|requirements?|budget|price|cost|prototype)\b',text,re.I))


def canonical_reviews(raw, pool, normalizations=None):
    errors=list(Draft202012Validator(review_schema(pool)).iter_errors(raw))
    if errors:raise ValueError(errors[0].message)
    result={"items":[],"excluded":[]}
    raw=copy.deepcopy(raw)
    for i, source in enumerate(pool):
        row=raw[f"C{i}"]
        if row['keep'] != bool(row['claims']):
            row.update(keep=True,claims=[{'text':source['text'],'priority':2}])
            row.pop('duplicate_of',None)
            if normalizations is not None:normalizations.append({'source':f'C{i}','action':'contradictory_disposition_preserved_for_p2'})
        if row['keep'] and 'duplicate_of' in row:
            row.pop('duplicate_of')
            row['claims']=[{'text':source['text'],'priority':max(c['priority'] for c in row['claims'])}]
            if normalizations is not None:normalizations.append({'source':f'C{i}','action':'ignored_duplicate_hint_on_kept_source'})
        if row['keep'] and source['type']=='decisions' and procedure_only(source['text']):
            result['excluded'].append({'index':i,'reason':'Routine introductions/icebreaker are not a project decision','category':'procedure'})
            row.update(keep=False,claims=[])
            continue
        if row['keep'] and source['type']!='decisions' and len(row['claims'])>1:
            row['claims']=[{'text':source['text'],'priority':max(c['priority'] for c in row['claims'])}]
            if normalizations is not None:normalizations.append({'source':f'C{i}','action':'nondecision_kept_as_original_single_task_or_problem'})
        if row['keep']:
            result['items'] += [{"type":source['type'],"text":c['text'],"priority":c['priority'],"members":[i],"reason":row['reason']}
                                for c in row['claims']]
        elif 'duplicate_of' not in row:
            result['excluded'].append({'index':i,'reason':row['reason'],'category':'not_project_relevant'})
    for i, source in enumerate(pool):
        row=raw[f"C{i}"]
        if 'duplicate_of' not in row:continue
        target=int(row['duplicate_of'][1:])
        if raw[f'C{target}']['keep'] and source['type']=='decisions' and pool[target]['type']=='actions':
            # E.g. a generic plan milestone already represented by the kept task.
            # This is an exclusion, not permission to manufacture an Action from a Decision.
            result['excluded'].append({'index':i,'reason':row['reason'],'category':'duplicate'})
            continue
        mismatch=(not raw[f'C{target}']['keep'] or source['type']!=pool[target]['type'] or
                  source['type']=='actions' and set(source.get('owner_ids',[]))!=set(pool[target].get('owner_ids',[])))
        if mismatch:
            # An unsafe semantic duplicate proposal must not delete the task or fail an otherwise
            # valid meeting. Preserve the unchanged source hypothesis for independent P2 checks.
            result['items'].append({'type':source['type'],'text':source['text'],'priority':2,'members':[i],
                                    'reason':'Duplicate proposal not applied; preserve original for P2: '+row['reason']})
            if normalizations is not None:normalizations.append({'source':f'C{i}','duplicate_of':row['duplicate_of'],
                                                                  'action':'preserved_original_for_p2'})
            continue
        for item in result['items']:
            if target in item['members']:item['members'].append(i)
    return result


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
        if any(not pool[i]["evidence_da_ids"] or not set(pool[i]["evidence_da_ids"]) <= set(delivered) for i in members):
            return "member_evidence_not_delivered"
        used |= members
    excluded = [row["index"] for row in result["excluded"]]
    if len(excluded) != len(set(excluded)) or used.intersection(excluded):
        return "contradictory_or_duplicate_exclusion"
    if used | set(excluded) != set(range(len(pool))):
        return "every_candidate_needs_an_explicit_disposition"
    return None


def ranked_cap(items, trace=None, action_limit=None, stage='select'):
    """Rank the whole pool, including recovered tasks, before applying unchanged limits."""
    limits={**config.MAX_ITEMS,'actions':config.MAX_ITEMS['actions'] if action_limit is None else action_limit}
    def attribution(item):
        fields=item.get('fields',{})
        ids=fields.get('owner',[]) if fields else item.get('owner_ids',[])
        basis=fields.get('owner_basis') if fields else item.get('owner_basis')
        return {s for s in ids or [] if isinstance(s,str) and s.strip()},basis=='explicit'
    def timed(item):
        return (item.get('fields') or item).get('deadline_basis')=='explicit'
    ranked = sorted(enumerate(items), key=lambda pair: (
        -pair[1].get("selection_priority", 2),
        -(pair[1]["type"] == "actions" and attribution(pair[1])[1]),
        -(pair[1]["type"] == "actions" and timed(pair[1])), pair[0]))
    chosen, owners = set(), set()
    remaining=[pair for pair in ranked if pair[1]['type']=='actions']
    for _ in range(limits['actions']):
        if not remaining:break
        pair=max(remaining,key=lambda p:(
            bool(attribution(p[1])[1] and attribution(p[1])[0]-owners),
            p[1].get('selection_priority',2),timed(p[1]),-p[0]))
        remaining.remove(pair);chosen.add(pair[0]);owners.update(attribution(pair[1])[0])
    counts, kept, dropped = {}, [], []
    for index,item in ranked:
        typ=item['type'];counts[typ]=counts.get(typ,0)+1
        if (index in chosen if typ=='actions' else counts[typ]<=limits[typ]):kept.append(item)
        else:dropped.append({'item':item,'reason':'type_limit_after_selection','rank':counts[typ],'limit':limits[typ]})
    if trace:
        trace.log(stage=stage, event="cap", before=items, kept=kept, dropped=dropped,
                  limits=limits, policy="checked_attribution_when_available_owner_coverage_then_priority_deadline_stable")
    return kept


def select_items(llm, pool, acts, cache=None, trace=None, defer_action_limit=False):
    if not pool:
        return []
    by_id = {a["id"]: a for a in acts}
    identifiers = list(dict.fromkeys(d for item in pool for d in item["evidence_da_ids"] if d in by_id))
    indexes = {d:i for i,d in enumerate(identifiers)}
    dialogue = [f"[{indexes[d]}] {by_id[d]['attributes']['role']}({by_id[d]['attributes']['participant']}): {by_id[d]['text']}"
                for d in identifiers]
    candidates = {f'C{i}': {**{k: item.get(k) for k in
        ("type", "text", "owner_ids", "status")},
        "evidence_lines":[indexes[d] for d in item['evidence_da_ids'] if d in indexes]}
        for i, item in enumerate(pool)}
    user = {"candidates": candidates, "observed_dialogue": dialogue,
            "output_format":"ONE review under EACH C-key: {keep:true|false, claims:[{text,priority}], reason, optional duplicate_of}. No output item IDs, owner or evidence fields."}
    key = hashlib.sha256(json.dumps([SYSTEM, config.ITEM_POLICY_VERSION, llm.model, user], sort_keys=True).encode()).hexdigest()
    saved = json.loads(cache.read_text(encoding="utf-8")) if cache and cache.exists() else {}
    raw = saved.get("response") if saved.get("key") == key else None
    source = "cache" if raw is not None else "model"
    for attempt in range(2):
        if raw is None:
            raw = llm.forced_tool(SYSTEM, json.dumps(user, ensure_ascii=False), "select_record_items", review_schema(pool),
                                     effort=config.EFFORT_CONSOLIDATE, stage="select")
        raw_response = copy.deepcopy(raw)
        normalizations=[]
        try:
            result=canonical_reviews(raw,pool,normalizations)
            error=selection_error(result,pool,identifiers)
        except ValueError as exc:
            result,error=None,str(exc)
        if trace:
            trace.log(stage="select", event="attempt", attempt=attempt, source=source,
                      input=user, raw_response=raw_response, response=result, normalizations=normalizations,
                      cache_origin=saved.get('origin') if source=='cache' else None, error=error)
        if not error:
            break
        user["correction"] = error
        user["previous_submission"] = raw
        raw, source = None, "model"
    else:
        raise ValueError("item_selection_invalid: " + error)
    if cache and source == "model":
        write_json(cache, {"key": key, "response": raw}, getattr(llm, "control", None))
    out = []
    for row in result["items"]:
        sources = [pool[i] for i in row["members"]]
        best = min(sources, key=lambda s: (s["type"] != row["type"], s.get("owner_basis") != "explicit"))
        item = copy.deepcopy(best)
        item.update(type=row["type"], text=_strip_indexes(row["text"]), evidence_da_ids=list(dict.fromkeys(
                        d for s in sources for d in s['evidence_da_ids'])),
                    source_item_indexes=row["members"], candidate_indexes=list(dict.fromkeys(
                        i for s in sources for i in s.get("candidate_indexes", []))),
                    selection_priority=row["priority"], selection_reason=row["reason"])
        out.append(item)
    if trace:
        trace.log(stage="select", event="completed", before=pool, items=out, excluded=result["excluded"])
    return ranked_cap(out, trace, action_limit=2*config.MAX_ITEMS['actions'] if defer_action_limit else None)
