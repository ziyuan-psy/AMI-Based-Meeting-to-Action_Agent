"""Evidence-grounded checks and the item controller. No gold labels or external messaging."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from jsonschema import Draft202012Validator

from . import config
from .agent import _blocks, _execute, build_reference, check_finish, meeting_of, tool_defs
from .prompts import P2_SYSTEM
from .linking import LINK_SCHEMA, link_error, witnesses
from .documents import SELECTION, evidence_error, selected_evidence, selected_references, validate_outcome
from .store import tokenize
from .assessment_protocol import compact_tool, canonical_assessment
from .p2_admission import ItemAllowanceExhausted
from .statement_revision import revision_tool, revision_candidates, deletion, unanchored_time_literals

CHECKS = ("statement", "owner", "deadline", "status", "conflict", "relation")
RESULTS = ("pass", "fail", "uncertain", "not_applicable")
ACTIONS = ("accept", "retrieve", "search_again", "revise_statement", "request_clarification", "stop")
FIELDS = ("statement", "owner", "deadline", "status", "related_items")
FINDING = {"type": "object", "properties": {
    "result": {"enum": list(RESULTS)}, "reason": {"type": "string", "minLength": 1},
    "quotes": {"type": "array", "items": {"type": "object", "properties": {
        "da_id": {"type": "string"}, "quote": {"type": "string", "minLength": 1}},
        "required": ["da_id", "quote"], "additionalProperties": False}},
    "fields": {"type": "array", "items": {"enum": list(FIELDS)}}},
    "required": ["result", "reason", "quotes", "fields"], "additionalProperties": False}


def assessment_tool(allow_prior, item_type=None, require_relation=False, require_evidence=False):
    fields = copy.deepcopy(tool_defs(allow_prior)[-1]["input_schema"])
    if item_type in (None, "actions"):
        fields["required"] += ["owner", "owner_basis", "deadline", "deadline_basis"]
    fields["additionalProperties"] = False
    finding_schemas = {k: copy.deepcopy(FINDING) for k in CHECKS}
    for name in CHECKS:
        finding_schemas[name]["required"].remove("fields")
        finding_schemas[name]["required"].remove("quotes")
        legacy_quote = copy.deepcopy(FINDING["properties"]["quotes"]["items"])
        legacy_quote["required"] = ["da_id"]
        finding_schemas[name]["properties"]["quotes"] = {"type": "array", "description": "Prefer a list of full delivered dialogue IDs; code binds their exact text. Legacy quote objects are also accepted.",
            "items": {"anyOf": [{"type": "string"}, legacy_quote]}}
        if name != "conflict":  # field identity is already determined by the check's name
            finding_schemas[name]["properties"]["fields"]["items"] = {"type": "string"}
        finding_schemas[name]["properties"]["documents"] = {"type": "array", "items": SELECTION,
            "description": "Explicitly assess scenario passages used for this check. Omit unused search hits. Documents supplement, never replace, current meeting evidence."}
        if require_evidence and name in ("statement", "status", "conflict"):
            finding_schemas[name]["allOf"] = [{"if": {"properties": {"result": {"const": "pass"}}},
                "then": {"required": ["quotes"], "properties": {"quotes": {"minItems": 1}}}}]
    finding_schemas["relation"]["properties"]["links"] = {"type": "array", "items": LINK_SCHEMA,
        "description": "Required when fields.related_items is nonempty; identify the change and both sets of quoted source IDs."}
    if require_relation:
        finding_schemas["relation"]["properties"]["result"]["enum"] = ["pass", "fail", "uncertain"]
    for name in ("statement", "status", "conflict") + (("owner", "deadline") if item_type == "actions" else ()):
        finding_schemas[name]["properties"]["result"]["enum"] = ["pass", "fail", "uncertain"]
    return {"name": "assess_item", "description": "Submit checked fields and evidence-grounded findings; code chooses the next action.",
            "input_schema": {"type": "object", "properties": {
                "fields": fields,
                "checks": {"type": "object", "properties": finding_schemas,
                           "required": list(CHECKS if item_type in (None, "actions") else ("statement", "status", "conflict", "relation")), "additionalProperties": False},
                "next_action": {"enum": list(ACTIONS)}, "reason": {"type": "string", "minLength": 1}},
                "required": ["fields", "checks"], "additionalProperties": False}}


def provisional(item):
    fields = {"owner": item.get("owner_ids") or None, "owner_basis": item.get("owner_basis", "missing"),
            "deadline": item.get("deadline"), "deadline_basis": item.get("deadline_basis", "missing"),
            "status": item["status"], "prior_context_needed": False, "related_items": [],
            "evidence_da_ids": list(item["evidence_da_ids"][:12]), "used_participant_lookup": False}
    if item["type"] != "actions":
        fields.update(owner=None, owner_basis="missing", deadline=None, deadline_basis="missing")
    return fields


def relation_tool():
    """Focus a remaining historical gap without resubmitting already-checked local fields."""
    finding = assessment_tool(True, require_relation=True)["input_schema"]["properties"]["checks"]["properties"]["relation"]
    return {"name": "assess_relation", "description": "Compare read prior records and current evidence: choose a supported relation, explain no relation, or remain uncertain and search/stop.",
            "input_schema": {"type": "object", "properties": {
                **{k: copy.deepcopy(v) for k, v in finding["properties"].items() if k != "fields"},
                "related_items": copy.deepcopy(tool_defs(True)[-1]["input_schema"]["properties"]["related_items"]),
                "next_action": {"enum": list(ACTIONS)}},
                "required": ["result", "reason", "quotes", "related_items"], "additionalProperties": False}}


def document_context_tool(item_type):
    checks = assessment_tool(False, item_type, require_evidence=True)["input_schema"]["properties"]["checks"]["properties"]
    return {"name": "assess_document_context",
            "description": "Check the current statement against delivered scenario passages. Select current dialogue IDs and document assessments. If status remains unresolved, optionally reassess it in status_check. You may request more context, change search, or stop pending.",
            "input_schema": {"type": "object", "properties": {
                **{k: copy.deepcopy(v) for k, v in checks["statement"]["properties"].items() if k != "fields"},
                "status_check": copy.deepcopy(checks["status"]),
                "status": copy.deepcopy(tool_defs(False)[-1]["input_schema"]["properties"]["status"]),
                "next_action": {"enum": list(ACTIONS)}},
                "required": ["result", "reason", "quotes", "documents"], "additionalProperties": False}}


def core_tool(allow_prior,item_type):
    base=compact_tool(allow_prior,item_type)['input_schema']['properties']
    return {'name':'assess_core','description':'Recheck the exact core task/choice/problem and actual contradictions, separately from auxiliary missing fields. Do not require a deadline or ceremonial promise for an assignment.',
        'input_schema':{'type':'object','properties':{k:copy.deepcopy(base[k]) for k in ('statement','conflict_check','next_action')},
                        'required':['statement','conflict_check','next_action'],'additionalProperties':False}}


def quote_line(store, da_id):
    mid = meeting_of(da_id)
    if not store.da_exists(mid, da_id):
        return None
    m = store.meeting(mid)
    act = m.acts[m.by_id[da_id]]
    return {"da_id": da_id, "meeting_id": mid, "text": act["text"], **act["attributes"]}


def bounded_result(data, limit=6000):
    """Deliver complete JSON and register only what the model actually receives."""
    if len(json.dumps(data, ensure_ascii=False)) <= limit:
        return data
    out = {"results": [], "omitted_results": len(data.get("results", []))}
    for row in data.get("results", []):
        candidate = {**out, "results": out["results"] + [row], "omitted_results": out["omitted_results"] - 1}
        if len(json.dumps(candidate, ensure_ascii=False)) <= limit:
            out = candidate
    if not out["results"]:
        out["error"] = "Result too large; use a smaller window or a narrower query."
    return out


def query_key(name, args):
    if "query" in args:
        args = {**args, "query": sorted(set(tokenize(args["query"])))}
    return name + json.dumps(args, sort_keys=True)


def bind_evidence(payload, observed, current, item_type=None):
    """Resolve explicitly selected link IDs to delivered text; never repair a fabricated quotation."""
    payload = copy.deepcopy(payload)
    checks, bindings = payload["checks"], {}
    if item_type and item_type != "actions":
        payload["fields"].update(owner=None, owner_basis="missing", deadline=None, deadline_basis="missing")
        for field in ("owner", "deadline"):
            checks.setdefault(field, {"result": "not_applicable", "reason": "field_not_applicable", "quotes": [], "fields": []})
    for name, finding in checks.items():
        resolved = []
        for quote in finding.get("quotes", []):
            quote = {"da_id": quote} if isinstance(quote, str) else copy.deepcopy(quote)
            identifier = quote["da_id"]
            if "quote" not in quote:
                quote["quote"] = observed.get(identifier, {}).get("text", "")
                if identifier in observed:
                    bindings.setdefault(name, []).append(identifier)
            resolved.append(quote)
        finding["quotes"] = resolved
        if name != "conflict":
            finding["fields"] = ["related_items" if name == "relation" else name]
    relation = checks["relation"]
    cited = {q["da_id"] for q in relation["quotes"]}
    for link in relation.get("links", []):
        for identifier in link["prior_evidence_da_ids"] + link["current_evidence_da_ids"]:
            if identifier not in cited and identifier in observed:
                relation["quotes"].append({"da_id": identifier, "quote": observed[identifier]["text"]})
                bindings.setdefault("relation", []).append(identifier)
                cited.add(identifier)
    # No-conflict describes the reviewed evidence scope, rather than an affirmative new fact.
    # Anchor an empty pass to the already-selected current statement support, not to invented evidence.
    conflict, statement = checks["conflict"], checks["statement"]
    if conflict["result"] == statement["result"] == "pass" and not conflict["quotes"]:
        valid = [q for q in statement["quotes"] if meeting_of(q["da_id"]) == current and q["da_id"] in observed
                 and q["quote"].strip() and q["quote"].strip().casefold() in observed[q["da_id"]]["text"].casefold()]
        conflict["quotes"] = copy.deepcopy(valid)
        if valid:
            bindings["conflict_scope"] = [q["da_id"] for q in valid]
    return payload, bindings


def evaluate(store, item, payload, observed, prior_seen, pages_seen, prior_attempted=False, documents_seen=None):
    """Validate provenance and combine semantic findings with mandatory code checks."""
    checks = copy.deepcopy(payload["checks"])
    proposed = payload["fields"]
    for name, finding in checks.items():
        finding.setdefault("fields", [])
        bad = [q for q in finding["quotes"] if not q["quote"].strip() or q["da_id"] not in observed
               or q["quote"].strip().casefold() not in observed[q["da_id"]]["text"].casefold()]
        if bad:
            finding.update(result="uncertain", reason="unobserved_or_inexact_quote", quotes=[])
        applicable = name not in ("owner", "deadline") or item["type"] == "actions"
        if not applicable:
            finding.update(result="not_applicable", reason="field_not_applicable", quotes=[])
            continue
        if name == "relation" and not proposed["related_items"] and (
                not store.allow_prior or store.order == 1 or not {"POSSIBLE_PRIOR", "STATUS_CHECK"}.intersection(item["flags"])):
            if finding["result"] == "not_applicable":
                continue
        elif finding["result"] == "not_applicable":
            finding.update(result="uncertain", reason="required_check_cannot_be_skipped")
        if finding["result"] == "pass" and not any(meeting_of(q["da_id"]) == store.current for q in finding["quotes"]):
            finding.update(result="uncertain", reason="current_meeting_support_required")
        document_error = evidence_error(store, finding, documents_seen or {})
        if document_error:
            finding.update(result="uncertain", reason=document_error)
    unread = set(proposed["evidence_da_ids"]) - set(observed)
    if unread:
        checks["statement"].update(result="uncertain", reason="finish_cites_unobserved_evidence")
    for name,text in [('statement',item['text'])]+([('deadline',proposed.get('deadline'))] if item['type']=='actions' else []):
        if checks[name]['result']=='pass':
            missing=unanchored_time_literals(text,checks[name]['quotes'],store.current)
            if missing:checks[name].update(result='uncertain',reason='unanchored_time_literal: '+', '.join(missing))
    if item["type"] == "actions":
        for field in ("owner", "deadline"):
            if proposed.get(field + "_basis") in ("missing", "ambiguous") or not proposed.get(field):
                checks[field].update(result="uncertain", reason=field + "_unresolved")
    related = proposed["related_items"]
    if (store.allow_prior and store.order > 1 and {"POSSIBLE_PRIOR", "STATUS_CHECK"}.intersection(item["flags"])
            and not related and not prior_attempted):
        checks["relation"].update(result="uncertain", reason="prior_context_not_checked")
    if store.allow_prior and store.order > 1 and "PRIOR_RECORDS_UNAVAILABLE" in item["flags"]:
        checks["relation"].update(result="uncertain", reason="prior_records_unavailable_not_a_confirmed_no_match")
    if related and (any(r["id"] not in prior_seen for r in related)
                    or not any(meeting_of(q["da_id"]) != store.current for q in checks["relation"]["quotes"])):
        checks["relation"].update(result="uncertain", reason="retrieve_prior_record_and_read_its_evidence")
    # The historical quote must support the actual selected record, not a different earlier topic.
    if related:
        cited = {q["da_id"] for q in checks["relation"]["quotes"]}
        if any(not cited.intersection(prior_seen.get(r["id"], {}).get("evidence", [])) for r in related):
            checks["relation"].update(result="uncertain", reason="selected_prior_item_evidence_not_read")
    error = link_error(store, related, checks["relation"], observed, prior_seen)
    if error:
        checks["relation"].update(result="uncertain", reason=error)
    # A model-declared auxiliary uncertainty must clear that field, not erase an independently
    # supported current statement. Keep the original proposed fields in trace for diagnosis.
    checked_proposal = copy.deepcopy(proposed)
    blocked = unresolved(checks)
    if 'status' in blocked:
        checked_proposal['status'] = 'unknown'
    if 'related_items' in blocked:
        checked_proposal['related_items'] = []
    if item['type'] == 'actions':
        for field in ('owner','deadline'):
            if field in blocked:
                basis = 'ambiguous' if proposed.get(field) or proposed.get(field+'_basis') == 'ambiguous' else 'missing'
                checked_proposal.update({field:None,field+'_basis':basis})
    fields, error = check_finish(store, item, checked_proposal, pages_seen)
    if error:
        checks["statement"].update(result="uncertain", reason="invalid_proposed_fields: " + error)
    if item.get("verify_project_context") and checks["statement"]["result"] == "pass":
        reviewed = any(finding["result"] == "pass" and d["result"] in ("supports", "context", "irrelevant")
                       and (documents_seen or {}).get(d["passage_id"]) is not None
                       and store.documents.valid_row(documents_seen[d["passage_id"]])
                       for finding in checks.values() for d in finding.get("documents", []))
        if not reviewed:
            checks["statement"].update(result="uncertain", reason="requested_document_context_not_checked")
    return checks, fields


def unresolved(checks):
    out = []
    for name, finding in checks.items():
        if finding["result"] in ("pass", "not_applicable"):
            continue
        fields = finding["fields"] if name == "conflict" else ["related_items" if name == "relation" else name]
        out += fields or ["statement"]
    return list(dict.fromkeys(out))


def decide(checks, suggested, can_lookup, attempted, no_progress, partial_accept=False):
    blocked = unresolved(checks)
    if not blocked:
        return "accept", "all_required_checks_passed"
    if (partial_accept and suggested=='accept' and checks['relation']['reason']=='prior_context_not_checked'
            and 'statement' not in blocked and can_lookup and no_progress<config.MAX_NO_PROGRESS):
        return 'retrieve','required_history_check_before_partial_accept'
    if partial_accept and suggested == 'accept' and 'statement' not in blocked:
        return 'request_clarification', 'supported_statement_with_unresolved_fields'
    if checks['statement']['reason'].startswith('unanchored_time_literal:') and suggested not in ('stop','request_clarification'):
        return 'revise_statement','named_time_requires_source_anchor_or_bounded_revision'
    if suggested == 'revise_statement' and 'statement' in blocked:
        return 'revise_statement', 'unsupported_qualifier_proposed_for_revision'
    if not can_lookup or no_progress >= config.MAX_NO_PROGRESS:
        return "request_clarification", "lookup_limit" if not can_lookup else "no_progress"
    if suggested in ("request_clarification", "stop"):
        return suggested, "unresolved_checks"
    action = "search_again" if attempted and suggested != "retrieve" and (suggested == "search_again" or no_progress > 0) else "retrieve"
    return action, "unresolved_checks"


def clarifications_for(item_key, item, checks, reason, available_evidence=()):
    questions = {"owner": "Who is responsible for this task?", "deadline": "What is the agreed deadline, in its original wording?",
                 "statement": "Should this be recorded as a substantive, supported " + item["type"] + " item?",
                 "status": "What is this item's status at the end of the current meeting?",
                 "related_items": "Which earlier record does this item continue or change, and how?"}
    requests = []
    for field in unresolved(checks):
        relevant = [v for k, v in checks.items() if (k == field or k == "relation" and field == "related_items"
                    or k == "conflict" and field in (v["fields"] or ["statement"])) and v["result"] not in ("pass", "not_applicable")]
        evidence = list(dict.fromkeys(q["da_id"] for v in relevant for q in v["quotes"]))
        requests.append({"request_id": hashlib.sha256((item.get("run_id", "") + ":" + item_key + ":" + field).encode()).hexdigest()[:16],
                         "item_key": item_key, "record_id": None, "field": field, "question": questions[field],
                         "item_text": item["text"], "reason": "; ".join(v["reason"] for v in relevant) or reason,
                         "evidence_da_ids": evidence, "available_evidence_da_ids": list(available_evidence),
                         "status": "open", "termination_reason": reason})
        if field == "statement" and any(v["reason"].startswith(("document_", "requested_document_")) for v in relevant):
            requests[-1]["question"] = "Which scenario passage supports this item's project context, and is it consistent with the current meeting evidence?"
    return requests


def safe_fields(store, item, proposed, checks, fields, documents_seen=None):
    """Keep supported claims; clear uncertain values instead of restoring extractor guesses."""
    blocked = unresolved(checks)
    statement_ok = checks.get("statement", {}).get("result") == "pass" and "statement" not in blocked
    if fields is None:
        fields = {"status": "unknown", "prior_context_needed": False, "related_items": [], "reference": []}
    else:
        fields = copy.deepcopy(fields)
    if "status" in blocked:
        fields["status"] = "unknown"
    if "related_items" in blocked:
        fields["related_items"], fields["prior_context_needed"] = [], False
    if item["type"] == "actions":
        for name in ("owner", "deadline"):
            if name in blocked or name not in fields:
                basis = "ambiguous" if proposed.get(name) or proposed.get(name + "_basis") == "ambiguous" else "missing"
                fields.update({name: None, name + "_basis": basis})
    # Only validated, read current evidence is kept when a historical relation was not verified.
    allowed = list(dict.fromkeys(q["da_id"] for c in checks.values() for q in c.get("quotes", [])
                                if c["result"] == "pass" and ("related_items" not in blocked or meeting_of(q["da_id"]) == store.current)))
    fields["reference"] = build_reference(store, allowed, fields.get("owner"), False, [])
    fields["reference"] += selected_references(selected_evidence(store, checks, documents_seen or {}, statement_ok, blocked))
    fields["prior_context_needed"] = bool(fields["related_items"]) or any(meeting_of(d) != store.current for d in allowed)
    return fields, statement_ok


def run_p2_agent(llm, store, item, budget, trace, item_key, progress=None):
    item = copy.deepcopy(item)
    seeds = [row for d in item["evidence_da_ids"][:12] if (row := quote_line(store, d)) is not None]
    context = {row["da_id"]: row for row in seeds}
    for row in seeds:
        for nearby in store.read_dialogue(meeting_of(row["da_id"]), row["da_id"], window=1):
            context.setdefault(nearby["da_id"], nearby)
    delivered = bounded_result({"results": list(context.values())})
    observed = {row["da_id"]: row for row in delivered["results"]}
    prior_seen, pages_seen, cache, documents_seen = {}, set(), {}, {}
    prior_attempted = False
    proposed = provisional(item)
    checks = {k: {"result": "uncertain", "reason": "not_checked", "quotes": [], "fields": []} for k in CHECKS}
    for k in ("owner", "deadline"):
        if item["type"] != "actions":
            checks[k].update(result="not_applicable", reason="field_not_applicable")
    if not store.allow_prior or store.order == 1 or not {"POSSIBLE_PRIOR", "STATUS_CHECK"}.intersection(item["flags"]):
        checks["relation"].update(result="not_applicable", reason="no_prior_check_required")
    fields = None
    history, calls, no_progress, invalid, semantic_stalls = [], 0, 0, 0, 0
    applicability_reassessed = False
    evidence_reassessed = False
    revision_attempted = False
    core_reassessed = False
    core_focus = False
    allow_prior = store.allow_prior and store.order > 1
    effort = config.EFFORT_LINK if (item.get("verify_project_context") or
        allow_prior and {"POSSIBLE_PRIOR", "STATUS_CHECK"}.intersection(item["flags"])) else config.EFFORT_AGENT
    assessor = assessment_tool(allow_prior, item["type"],
                               bool(allow_prior and {"POSSIBLE_PRIOR", "STATUS_CHECK"}.intersection(item["flags"])),
                               require_evidence=bool(item.get("verify_project_context")))
    compact_assessor = compact_tool(allow_prior,item['type'],
        bool(allow_prior and {'POSSIBLE_PRIOR','STATUS_CHECK'}.intersection(item['flags'])))
    compact_flow = False
    legacy_input = bool(getattr(llm,'p2_legacy_input',False)
                        or getattr(getattr(llm,'client',None),'p2_legacy_input',False))
    lookup_tools = [t for t in tool_defs(allow_prior) if t["name"] != "finish"]
    terminal = [{"name": name, "description": "Leave unresolved checks pending for human review.",
                 "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}
                for name in ("request_clarification", "stop")]
    initial = {"item": {k: item.get(k) for k in ("type", "text", "flags")}, "fields": proposed,
               "current_meeting": store.current, "earlier_meetings": store.visible_ids("prior"),
               "roster": store.roster(store.current), "observed_dialogue": list(observed.values())}
    initial["peer_items"] = item.get("peer_items", [])
    if not legacy_input:
        initial['provisional_values'] = {k:v for k,v in initial.pop('fields').items()
                                         if k not in ('evidence_da_ids','prior_context_needed','used_participant_lookup')}
        initial['candidate_evidence_da_ids'] = [d for d in item['evidence_da_ids'] if d in observed]
    if item.get("verification_request"):
        initial["verification_request"] = item["verification_request"]
    if item.get("verify_project_context"):
        initial["verify_project_context"] = True
    messages = [{"role": "user", "content": json.dumps(initial, ensure_ascii=False)}]
    phase, next_action = "check", "retrieve"

    def snapshot(state="running", reason="in_progress"):
        published, recordable = safe_fields(store, item, proposed, checks, fields, documents_seen)
        return {"item_key": item_key, "record_id": None, "item_type": item["type"], "item_text": item["text"],
                "state": state, "recordable": recordable, "fields": published,
                "draft_fields": proposed, "checks": checks, "unresolved_fields": unresolved(checks),
                "observed_evidence_da_ids": list(observed),
                "initial_context": initial,
                "observed_documents": list(documents_seen.values()),
                "document_evidence": selected_evidence(store, checks, documents_seen, recordable, unresolved(checks)),
                "relation_evidence": witnesses(item_key, item, published, checks["relation"], prior_seen,
                                                recordable, "related_items" in unresolved(checks)),
                "history": history, "lookups": calls, "termination_reason": reason,
                "clarifications": clarifications_for(item_key, item, checks, reason, observed) if state == "pending" else []}

    def save():
        if progress:
            progress(copy.deepcopy(snapshot()))

    def record(step, action, outcome, **data):
        event = {"item": item_key, "step": step, "flags": item["flags"], "action": action, "outcome": outcome, **data}
        history.append(copy.deepcopy(event))
        trace.log(**copy.deepcopy(event))
        save()

    def finish(state, reason):
        terminal_event = {"item": item_key, "step": history[-1]["step"] if history else 0,
                          "flags": item["flags"], "action": "accept" if state == "accepted" else "stop",
                          "outcome": state, "termination_reason": reason, "unresolved_fields": unresolved(checks)}
        history.append(copy.deepcopy(terminal_event))
        trace.log(**terminal_event)
        outcome = snapshot(state, reason)
        if progress:
            progress(copy.deepcopy(outcome))
        return outcome

    def offer_core_recheck(step):
        """One semantic review before either assessment or a terminal tool discards a read core."""
        nonlocal core_reassessed,core_focus,phase
        provenance_errors=('unobserved_','finish_cites_','current_meeting_support_required','invalid_proposed_fields:',
                           'document_','requested_document_','unanchored_time_literal:')
        core_findings=[checks[k] for k in ('statement','conflict') if checks[k]['result'] not in ('pass','not_applicable')]
        if (legacy_input or core_reassessed or 'statement' not in unresolved(checks)
                or any(f['reason'].startswith(provenance_errors) for f in core_findings)
                or item.get('verify_project_context')
                or not any(q['da_id'] in observed and meeting_of(q['da_id'])==store.current for q in checks['statement']['quotes'])):
            return None
        core_reassessed,core_focus,phase=True,True,'check'
        record(step,'core_recheck_offered','focused_review',reason='semantic_core_uncertainty_with_read_evidence')
        return {'current_item_text':item['text'],'checks':checks,
            'core_recheck_instruction':'One focused recheck is available before leaving pending. Assess the exact core claim and actual contradictory evidence. A manager naming a role and task, especially with that role acknowledging, can establish an assignment without a deadline or a ceremonial I promise. Missing auxiliary fields and limited global coverage are not themselves contradictions. Pass conflict only within delivered evidence when no contradiction is found. A suggestion/capability alone is not a commitment. Select only delivered evidence, or stop/clarify pending. No new source access is granted.'}

    save()
    for step in range(1, config.MAX_AGENT_STEPS + 1):
        suffixes=revision_candidates(item['text'])
        current_ids=[d for d in observed if meeting_of(d)==store.current]
        can_revise=bool(not revision_attempted and suffixes and current_ids)
        relation_focus = (allow_prior and "related_items" in unresolved(checks)
                          and checks["statement"]["result"] == checks["conflict"]["result"] == "pass")
        document_focus = checks["statement"]["reason"] in ("requested_document_context_not_checked", "document_passage_not_delivered")
        if phase == "revision":
            tools, choice = [revision_tool(item['text'],current_ids)] + terminal, {"type": "any"}
        elif phase == "check":
            read_history = any(set(r["evidence"]).intersection(observed) for r in prior_seen.values())
            focused = relation_focus and read_history
            tools = [relation_tool(), compact_assessor] if focused else [compact_assessor]
            choice = {"type": "tool", "name": "assess_relation" if focused else "assess_item"}
            if documents_seen and item.get("verify_project_context") and checks["statement"]["result"] != "pass":
                tools = [document_context_tool(item["type"]), compact_assessor]
                choice = {"type": "tool", "name": "assess_document_context"}
            if core_focus:
                tools,choice=[core_tool(allow_prior,item['type'])]+terminal,{'type':'any'}
        else:
            allowed = lookup_tools if next_action == "retrieve" else [t for t in lookup_tools if t["name"] in (
                "search_dialogue", "get_prior_items", "search_project_doc")]
            # Focus history without denying current dialogue needed by another unresolved field.
            if relation_focus:
                allowed = [copy.deepcopy(t) for t in allowed if t["name"] in (
                    "get_prior_items", "read_prior_item", "search_dialogue", "read_dialogue", "search_project_doc", "read_project_doc")]
                for definition in allowed:
                    if definition["name"] == "search_dialogue" and not (set(unresolved(checks))-{'related_items'}):
                        definition["input_schema"]["properties"]["scope"] = {"enum": ["prior"]}
                    elif definition["name"] == "read_dialogue" and not (set(unresolved(checks))-{'related_items'}):
                        definition["input_schema"]["properties"]["meeting_id"] = {"enum": store.visible_ids("prior")}
            if document_focus:
                allowed = [t for t in allowed if t["name"] in ("search_project_doc", "read_project_doc")]
                if documents_seen and next_action == "retrieve":
                    allowed = [t for t in allowed if t["name"] != "search_project_doc"]
            if can_revise and not document_focus and checks["statement"]["result"] in ("uncertain", "fail"):
                allowed = allowed + [revision_tool(item['text'],current_ids)]
            # A retrieve action verifies already-returned records. A new item search remains available
            # through search_again; the Agent still chooses the record, evidence, query or a pending stop.
            if legacy_input and next_action == "retrieve" and prior_seen and checks["relation"]["result"] == "uncertain":
                allowed = [t for t in allowed if t["name"] != "get_prior_items"]
            tools, choice = allowed + terminal, {"type": "any"}
        try:
            response = llm.create(P2_SYSTEM, messages, tools, choice, effort='medium' if core_focus else effort, stage="p2_check" if phase == "check" else "agent")
        except ItemAllowanceExhausted:
            return finish('pending','p2_call_allowance_exhausted')
        blocks = _blocks(response)
        messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no tool call)"}]})
        uses = [b for b in blocks if b["type"] == "tool_use"]
        results = [{"type": "tool_result", "tool_use_id": u["id"], "is_error": True,
                    "content": "One action per step; this extra action was not executed."} for u in uses[1:]]
        error, feedback = None, None
        if not uses:
            error = "Call exactly one permitted tool."
        else:
            use = uses[0]
            name, args = use["name"], use["input"]
            raw_args = copy.deepcopy(args)
            normalizations = []
            definition = next((t for t in tools if t["name"] == name), None)
            if isinstance(args,dict):
                args=copy.deepcopy(args)
                if name in ('assess_item','assess_core') and 'confidence' in args:
                    args.pop('confidence')
                    normalizations.append('ignored_advisory_confidence')
                if name in ('assess_item','assess_core') and isinstance(args.get('conflict_check'),dict):
                    conflict=args['conflict_check']
                    if conflict.get('result')=='pass' and 'affected_fields' not in conflict:
                        conflict['affected_fields']=[]
                        normalizations.append('empty_affected_fields_for_no_conflict')
                if name=='read_dialogue' and 'meeting_id' not in args and isinstance(args.get('da_id'),str):
                    args['meeting_id']=meeting_of(args['da_id'])
                    normalizations.append('meeting_from_explicit_dialogue_id')
            if legacy_input and name == "assess_item" and isinstance(args, dict) and isinstance(args.get("fields"), dict):
                # Decode saved v1 responses for offline replay; live tools advertise only the flat format.
                definition = assessor if definition else None
                args = copy.deepcopy(args)
                for key in tool_defs(allow_prior)[-1]["input_schema"]["properties"]:
                    if key in args and key in args["fields"] and args[key] == args["fields"][key]:
                        del args[key]
                        normalizations.append("removed_identical_root_duplicate:" + key)
                if args.get("documents") == []:
                    del args["documents"]
                    normalizations.append("removed_empty_root_documents")
            schema_errors = list(Draft202012Validator(definition["input_schema"]).iter_errors(args)) if definition else []
            if not definition:
                error = "Use one of: " + ", ".join(t["name"] for t in tools)
            elif schema_errors:
                error = "Correct these argument fields: " + "; ".join(
                    "/".join(map(str, e.absolute_path)) + ": " + e.message for e in schema_errors[:5])[:1200]
                if name=='revise_statement':
                    error+=' Choose one exact advertised suffix, a nonempty reason and delivered current evidence IDs, or stop pending. Empty values are not a revision.'
            elif name=='get_prior_items' and re.fullmatch(r'[A-Za-z]+\d+[a-d]',args['query'].strip(),re.I):
                error='Search earlier record TEXT using subject/property keywords, not a meeting ID. Choose a content query, search again or stop pending.'
            elif name in ("request_clarification", "stop"):
                invalid = 0
                feedback=offer_core_recheck(step)
                if feedback is None:
                    record(step, name, "pending", reason=args["reason"], decision=name)
                    return finish("pending", name)
            elif name == "revise_statement":
                if revision_attempted or checks["statement"]["result"] not in ("uncertain", "fail"):
                    return finish("pending", "statement_revision_not_permitted")
                revision_attempted = True
                try:
                    revised = deletion(item["text"], args["remove_suffix"], args["evidence_da_ids"], observed, store.current)
                except ValueError as exc:
                    record(step, name, "rejected_revision", reason=str(exc), raw_tool_input=raw_args)
                    return finish("pending", "statement_revision_rejected")
                old_text = item["text"]
                item["text"] = revised
                # Revoke every previous verdict, field and link; source access remains unchanged.
                proposed, fields = provisional(item), None
                checks = {k: {"result": "uncertain", "reason": "statement_revised_requires_recheck", "quotes": [], "fields": []}
                          for k in CHECKS}
                for k in ("owner", "deadline"):
                    if item["type"] != "actions":
                        checks[k].update(result="not_applicable", reason="field_not_applicable")
                if not allow_prior or not {"POSSIBLE_PRIOR", "STATUS_CHECK"}.intersection(item["flags"]):
                    checks["relation"].update(result="not_applicable", reason="no_prior_check_required")
                record(step, name, "revised_requires_recheck", before_text=old_text, after_text=revised,
                       reason=args["reason"], evidence_da_ids=args["evidence_da_ids"], raw_tool_input=raw_args)
                feedback = {"current_item_text": revised, "mandatory_action": "assess_item",
                            "instruction": "The old verdicts and links were revoked. Reassess ALL applicable checks for this exact text using delivered evidence. No second revision is allowed."}
                phase, no_progress, semantic_stalls = "check", 0, 0
            elif phase == "check":
                invalid = 0
                submitted = copy.deepcopy(args)
                if name == 'assess_item' and 'statement' in args:
                    args = canonical_assessment(args,item['type'])
                    compact_flow = True
                if name == 'assess_core':
                    converted={}
                    for root,key in [('statement','statement'),('conflict_check','conflict')]:
                        finding=copy.deepcopy(args[root]);finding['quotes']=finding.pop('evidence_da_ids')
                        finding['fields']=finding.pop('affected_fields',[]) if key=='conflict' else ['statement']
                        converted[key]=finding
                    args={'fields':{**proposed,'evidence_da_ids':list(args['statement']['evidence_da_ids'])},
                          'checks':{**checks,**converted},'next_action':args['next_action']}
                    compact_flow=True
                    core_focus=False
                elif name == "assess_relation":
                    relation = {k: args[k] for k in ("result", "reason", "quotes", "links", "documents") if k in args}
                    relation["fields"] = ["related_items"]
                    args = {"fields": {**proposed, "related_items": args["related_items"],
                              "evidence_da_ids": list(dict.fromkeys(proposed["evidence_da_ids"] + [q if isinstance(q, str) else q["da_id"] for q in args["quotes"]]))},
                            "checks": {**checks, "relation": relation},
                            **({"next_action": args["next_action"]} if "next_action" in args else {})}
                elif name == "assess_document_context":
                    finding = {k: args[k] for k in ("result", "reason", "quotes", "documents")}
                    finding["fields"] = ["statement"]
                    merged = {**checks, "statement": finding}
                    if "status_check" in args:
                        merged["status"] = args["status_check"]
                    ids = [q if isinstance(q, str) else q["da_id"] for q in args["quotes"]]
                    ids += [q if isinstance(q, str) else q["da_id"] for q in args.get("status_check", {}).get("quotes", [])]
                    args = {"fields": {**proposed, "status": args.get("status", proposed["status"]),
                              "evidence_da_ids": list(dict.fromkeys(proposed["evidence_da_ids"] + ids))},
                            "checks": merged, **({"next_action": args["next_action"]} if "next_action" in args else {})}
                submitted_checks = copy.deepcopy(args["checks"])
                args, bindings = bind_evidence(args, observed, store.current, item["type"])
                before = json.dumps({k: (v["result"], v["reason"], v["fields"]) for k, v in checks.items()
                                     if v["result"] not in ("pass", "not_applicable")}, sort_keys=True)
                proposed = copy.deepcopy(args["fields"])
                checks, fields = evaluate(store, item, args, observed, prior_seen, pages_seen, prior_attempted, documents_seen)
                after = json.dumps({k: (v["result"], v["reason"], v["fields"]) for k, v in checks.items()
                                    if v["result"] not in ("pass", "not_applicable")}, sort_keys=True)
                semantic_stalls = semantic_stalls + 1 if calls and before == after else 0
                next_action, reason = decide(checks, args.get("next_action", "retrieve"), calls < config.MAX_CALLS_PER_ITEM and budget.left() > 0,
                                             calls > 0, max(no_progress, semantic_stalls), partial_accept=compact_flow)
                reassess = (not applicability_reassessed and item["type"] != "actions" and unresolved(checks)
                            and args.get("next_action") in ("request_clarification", "stop")
                            and any(submitted_checks.get(k, {}).get("result") in ("fail", "uncertain") for k in ("owner", "deadline")))
                if reassess:
                    applicability_reassessed = True
                    next_action, reason = "reassess", "clarification_hint_used_inapplicable_fields"
                correct_applicability = reassess
                repair_evidence = (not evidence_reassessed and args.get("next_action") not in ("stop", "request_clarification")
                    and any(f["reason"] == "current_meeting_support_required"
                            and submitted_checks.get(k, {}).get("result") == "pass"
                            and not submitted_checks[k].get("quotes") for k, f in checks.items()))
                if repair_evidence:
                    evidence_reassessed = True
                    reassess = True
                    next_action, reason = "reassess", "missing_current_evidence_selection"
                record(step, name, "checked", checks=checks, decision=next_action, reason=reason,
                       assessed_text=item["text"],
                       requested_action=args.get("next_action"), proposed_fields=proposed,
                       submitted_checks=submitted_checks, submitted_assessment=submitted, evidence_bindings=bindings,
                       raw_tool_input=raw_args, protocol_normalizations=normalizations)
                if next_action == "accept":
                    return finish("accepted", reason)
                if next_action == "revise_statement":
                    if revision_attempted or document_focus:
                        return finish("pending", "statement_revision_limit" if revision_attempted else "document_context_required")
                    feedback = {"decision": next_action, "current_item_text": item["text"], "checks": checks,
                                'available_suffixes':suffixes,'delivered_current_evidence_ids':current_ids,
                                "instruction": "Choose one deletion-only revision of an unsupported trailing qualifier, or stop pending. Do not remove the actor, task, negation, modality or a condition."}
                    phase = "revision" if can_revise else "lookup"
                    if not can_revise:
                        next_action='retrieve'
                        feedback.update(decision=next_action,instruction='No safe trailing qualifier is available for deletion. Do not call revise_statement with empty values. Choose a useful lookup, search again or stop pending.')
                        record(step,'revision_unavailable','lookup_or_pending',reason='no_safe_trailing_qualifier')
                    results.append({"type": "tool_result", "tool_use_id": use["id"], "is_error": False,
                                    "content": json.dumps(feedback, ensure_ascii=False)})
                    messages.append({"role": "user", "content": results})
                    continue
                if next_action in ("request_clarification", "stop"):
                    feedback=offer_core_recheck(step)
                    if feedback is not None:
                        results.append({'type':'tool_result','tool_use_id':use['id'],'is_error':False,'content':json.dumps(feedback,ensure_ascii=False)})
                        messages.append({'role':'user','content':results})
                        continue
                    return finish("pending", reason)
                feedback = {"decision": next_action, "unresolved_fields": unresolved(checks), "checks": checks}
                if documents_seen:
                    feedback["delivered_document_passages"] = list(documents_seen)
                    feedback["document_instruction"] = "Select relevant delivered passage IDs in checks.<name>.documents with result and reason; unused search hits are not citations. Read more context or search again if needed."
                elif item.get("verify_project_context"):
                    feedback["document_instruction"] = "The user explicitly requested a scenario-context check. Current dialogue support does not complete that request. Search the project document, inspect useful context and select passages, or stop pending. You choose queries and pages."
                if correct_applicability:
                    feedback["applicability_correction"] = "This is not an Action: owner and deadline do not apply. Reassess the corrected checks and choose retrieve/search/stop for the actual unresolved fields. You may still stop pending if those cannot be settled."
                if repair_evidence:
                    feedback["evidence_selection_correction"] = "A pass needs a supporting current dialogue ID. Select an actually delivered current ID for the indicated check, or mark it uncertain; document IDs cannot replace meeting evidence."
                    feedback["available_current_dialogue"] = [r for d, r in observed.items() if meeting_of(d) == store.current]
                bad_ids = {q["da_id"] for finding in args["checks"].values() for q in finding["quotes"]
                           if q["da_id"] in observed and (not q["quote"].strip()
                           or q["quote"].strip().casefold() not in observed[q["da_id"]]["text"].casefold())}
                if bad_ids:
                    feedback["quote_corrections"] = {d: observed[d]["text"] for d in sorted(bad_ids)}
                    feedback["quote_instruction"] = "Copy a short contiguous substring exactly, including repeated words; do not paraphrase a quote."
                if "related_items" in unresolved(checks) and prior_seen:
                    feedback["retrieved_candidates"] = [{"id": r["id"], "text": r["text"], "evidence": r["evidence"]}
                                                        for r in prior_seen.values()]
                    feedback["history_instruction"] = "Record search returned locators, not dialogue. You may read_prior_item with a candidate ID to verify its evidence, change the search, or stop pending."
                phase = "check" if reassess else "lookup"
            else:
                invalid = 0
                if calls >= config.MAX_CALLS_PER_ITEM or not budget.take():
                    record(step, name, "lookup_budget_exhausted", decision="request_clarification")
                    return finish("pending", "lookup_limit")
                calls += 1
                key = query_key(name, args)
                if key in cache:
                    data, new_info = bounded_result({**cache[key], "duplicate": True}), False
                else:
                    try:
                        data = bounded_result(_execute(store, name, args))
                    except (PermissionError, KeyError, ValueError, TypeError) as exc:
                        data = {"error": type(exc).__name__, "results": []}
                    cache[key] = data
                    if not data.get("error") and (name == "get_prior_items" or name == "search_dialogue" and args.get("scope") == "prior"):
                        prior_attempted = True
                    new_info = False
                    for row in data.get("results", []):
                        if name in ("search_dialogue", "read_dialogue") or name == "read_prior_item" and row.get("kind") == "dialogue":
                            d = row["da_id"]
                            if store.da_exists(meeting_of(d), d):
                                new_info |= d not in observed
                                observed[d] = row
                        elif name == "get_prior_items" or name == "read_prior_item" and row.get("kind") == "prior_item":
                            new_info |= row["id"] not in prior_seen
                            prior_seen[row["id"]] = row
                            prior_attempted = True
                        elif name in ("search_project_doc", "read_project_doc"):
                            if not store.documents.valid_row(row):
                                continue
                            new_info |= row["passage_id"] not in documents_seen
                            documents_seen[row["passage_id"]] = row
                            pages_seen.add(row["page"])
                no_progress = 0 if new_info else no_progress + 1
                record(step, name, "lookup", decision=next_action, args=args, tool_result=data, new_info=new_info)
                feedback = data
                phase = "check"
                if (not legacy_input and name=='get_prior_items' and prior_seen and relation_focus
                        and not any(set(r['evidence']).intersection(observed) for r in prior_seen.values())):
                    # Search locators are not evidence. Let the Agent choose a record/read or a new
                    # search before asking it to assess the same incomplete history again.
                    phase,next_action='lookup','retrieve'
                    feedback={**data,'history_instruction':'These records are candidates, not dialogue. Choose read_prior_item with an actual returned ID, read_dialogue using a cited locator, change the search, or stop pending. No relation is established until supporting evidence is read.'}
                if no_progress >= config.MAX_NO_PROGRESS:
                    return finish("pending", "no_progress")
            results.append({"type": "tool_result", "tool_use_id": use["id"], "is_error": bool(error),
                            "content": json.dumps(feedback, ensure_ascii=False) if not error else error})
        if error:
            invalid += 1
            record(step, uses[0]["name"] if uses else None, "invalid_action", error=error,
                   raw_tool_input=raw_args if uses else None)
            if not uses:
                messages.append({"role": "user", "content": error})
            if invalid >= config.MAX_INVALID_ACTIONS:
                return finish("pending", "invalid_action_limit")
        if results:
            messages.append({"role": "user", "content": results})
    return finish("pending", "step_limit")


def validate_outputs(p2, clarifications, record, store):
    schema = json.loads(config.P2_SCHEMA.read_text(encoding="utf-8"))
    errors = ["p2/" + "/".join(map(str, e.absolute_path)) + ": " + e.message
              for e in Draft202012Validator(schema).iter_errors({"p2": p2, "clarifications": clarifications})]
    if errors:
        return errors
    if p2["run_id"] != clarifications["run_id"] or p2["meeting_id"] != clarifications["meeting_id"]:
        errors.append("p2: sidecars identify different runs/meetings")
    records = {r["id"]: r for r in record if r["type"] != "abstract"}
    outcomes = {i["item_key"]: i for i in p2["items"]}
    if len(outcomes) != len(p2["items"]):
        errors.append("p2: duplicate item key")
    expected_requests = [r for i in p2["items"] for r in i["clarifications"]]
    if expected_requests != clarifications["requests"]:
        errors.append("p2: clarification index differs from item requests")
    for item in p2["items"]:
        errors += validate_outcome(store, item)
        if item["state"] == "accepted" and (item["unresolved_fields"] or not item["recordable"]):
            errors.append("p2: accepted item still has unresolved checks")
        if item["recordable"] and (item["checks"]["statement"]["result"] != "pass" or "statement" in item["unresolved_fields"]):
            errors.append("p2: an unsupported statement was marked recordable")
        if item['recordable'] and unanchored_time_literals(item['item_text'],item['checks']['statement']['quotes'],store.current):
            errors.append('p2: published statement contains an unanchored weekday/date')
        if item['recordable'] and item['item_type']=='actions' and unanchored_time_literals(item['fields'].get('deadline'),item['checks']['deadline']['quotes'],store.current):
            errors.append('p2: published deadline contains an unanchored weekday/date')
        if item["state"] == "pending" and {r["field"] for r in item["clarifications"]} != set(item["unresolved_fields"]):
            errors.append("p2: a pending field has no clarification request")
        if item["state"] == "accepted" and item["clarifications"]:
            errors.append("p2: an accepted item has open clarification requests")
        if item["unresolved_fields"] != unresolved(item["checks"]):
            errors.append("p2: unresolved fields do not match checks")
        for finding in item["checks"].values():
            for quote in finding["quotes"]:
                row = quote_line(store, quote["da_id"])
                if (quote["da_id"] not in item["observed_evidence_da_ids"] or row is None
                        or not quote["quote"].strip() or quote["quote"].strip().casefold() not in row["text"].casefold()):
                    errors.append("p2: check quotes do not match observed dialogue")
        if item["recordable"] != bool(item["record_id"]) or item["record_id"] and item["record_id"] not in records:
            errors.append("p2: record mapping is invalid")
        if item["record_id"] in records:
            row = records[item["record_id"]]
            if row["text"] != item["item_text"]:
                errors.append("p2: published text differs from the checked statement")
            if any(row.get(k) != v for k, v in item["fields"].items()):
                errors.append("p2: published fields differ from the checked fields")
        revisions = [e for e in item["history"] if e.get("outcome") == "revised_requires_recheck"]
        if revisions:
            revision = revisions[-1]
            rechecks = [e for e in item["history"] if e.get("outcome") == "checked" and e["step"] > revision["step"]]
            if len(revisions) != 1 or revision["after_text"] != item["item_text"]:
                errors.append("p2: revision chain is invalid")
            if item["recordable"] and (not rechecks or rechecks[-1].get("assessed_text") != item["item_text"]):
                errors.append("p2: a revised statement was published without reassessment")
        published_links = {(r["id"], r["relation"]) for r in item["fields"].get("related_items", [])}
        evidence_witnesses = item.get("relation_evidence", [])
        supported = {(w["prior_item_id"], w["relation"]) for w in evidence_witnesses if w["state"] == "supported"}
        if published_links != supported:
            errors.append("p2: published links lack matching before/after evidence witnesses")
        if published_links:
            actual_prior = {p["id"]: p for p in store.prior_items()}
            prior_seen = {}
            for witness in evidence_witnesses:
                if witness["state"] != "supported":
                    continue
                old = actual_prior.get(witness["prior_item_id"])
                if old is None:
                    errors.append("p2: evidence witness targets an unreadable record")
                    continue
                metadata = store._prior_metadata(old)
                prior_seen[old["id"]] = metadata
                if any(witness["before"].get(k) != metadata.get(k) for k in metadata):
                    errors.append("p2: evidence witness differs from the historical record snapshot")
            error = link_error(store, item["fields"]["related_items"], item["checks"]["relation"],
                               dict.fromkeys(item["observed_evidence_da_ids"]), prior_seen)
            if error:
                errors.append("p2: " + error)
            expected = witnesses(item["item_key"], {"type": item["item_type"], "text": item["item_text"]},
                item["fields"], item["checks"]["relation"], prior_seen, item["recordable"], False)
            # Delivery metadata (paging/kind/score) may vary; the claim and evidence must match exactly.
            strip_before = lambda rows: [{k: v for k, v in row.items() if k != "before"} for row in rows if row["state"] == "supported"]
            if strip_before(evidence_witnesses) != strip_before(expected):
                errors.append("p2: evidence witness does not match the assessed change and quotes")
    if p2["enabled"] and set(records) != {i["record_id"] for i in p2["items"] if i["recordable"]}:
        errors.append("p2: a record was published without a corresponding checked outcome")
    ids = set()
    for request in clarifications["requests"]:
        item = outcomes.get(request["item_key"])
        if request["request_id"] in ids or not item or request["record_id"] != item["record_id"]:
            errors.append("p2: clarification identity/mapping is invalid")
        if item and not set(request["available_evidence_da_ids"] + request["evidence_da_ids"]) <= set(item["observed_evidence_da_ids"]):
            errors.append("p2: clarification cites evidence not delivered to its agent")
        ids.add(request["request_id"])
        if any(not store.da_exists(meeting_of(d), d) for d in request["evidence_da_ids"] + request["available_evidence_da_ids"]):
            errors.append("p2: clarification references unreadable dialogue")
    return errors
