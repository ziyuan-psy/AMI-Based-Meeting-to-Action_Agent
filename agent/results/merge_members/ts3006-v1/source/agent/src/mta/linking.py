"""Per-link evidence gates and reviewable before/after witnesses. No inferred automatic links."""
from __future__ import annotations

import copy

from .agent import RELATIONS

LINK_SCHEMA = {"type": "object", "properties": {
    "id": {"type": "string", "description": "The exact existing PRIOR RECORD id returned by a history tool. Do not create a new link id or prefix the record id."}, "relation": {"enum": RELATIONS},
    "changed_fields": {"type": "array", "uniqueItems": True,
                       "items": {"enum": ["text", "scope", "owner", "deadline", "status"]},
                       "description": "Must be [] for continues; updates/replaces must identify at least one changed field. Choose the relationship from evidence; code never changes it for you."},
    "reason": {"type": "string", "minLength": 1},
    "prior_evidence_da_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
    "current_evidence_da_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}}},
    "required": ["id", "relation", "changed_fields", "reason", "prior_evidence_da_ids", "current_evidence_da_ids"],
    "additionalProperties": False}


def link_error(store, related, finding, observed, prior_seen):
    links = finding.get("links", [])
    expected = {(r["id"], r["relation"]) for r in related}
    actual = {(r["id"], r["relation"]) for r in links}
    if len(actual) != len(links) or len(expected) != len(related) or actual != expected:
        return "per_link_assessment_required_for_each_related_item"
    cited = {q["da_id"] for q in finding["quotes"]}
    prior_ids = set(store.visible_ids("prior"))
    for link in links:
        target = prior_seen.get(link["id"])
        if target is None or link["id"].split(".")[0] not in prior_ids:
            return "related_item_not_retrieved_or_outside_visible_history"
        old, current = set(link["prior_evidence_da_ids"]), set(link["current_evidence_da_ids"])
        if not old <= set(target["evidence"]):
            return "prior_quotes_must_support_the_selected_record"
        if not (old | current) <= cited.intersection(observed):
            return "read_and_quote_each_links_prior_and_current_evidence"
        if any(d.split(".")[0] not in prior_ids for d in old) or any(d.split(".")[0] != store.current for d in current):
            return "link_evidence_has_wrong_temporal_scope"
        if link["relation"] == "continues" and link["changed_fields"]:
            return "continues_cannot_also_claim_changed_fields"
        if link["relation"] in ("updates", "replaces") and not link["changed_fields"]:
            return "updates_or_replaces_requires_an_identified_change"
        if link["relation"] == "completes" and target["type"] != "actions":
            return "completes_requires_an_earlier_action"
        if link["relation"] == "resolves" and target["type"] != "problems":
            return "resolves_requires_an_earlier_problem"
    return None


def witnesses(item_key, item, proposed, finding, prior_seen, recordable, blocked):
    quotes = {q["da_id"]: q["quote"] for q in finding["quotes"]}
    output = []
    for link in finding.get("links", []):
        target = prior_seen.get(link["id"])
        if target is None:
            continue
        output.append({"prior_item_id": link["id"], "relation": link["relation"],
                       "state": "supported" if finding["result"] == "pass" and recordable and not blocked else "pending",
                       "changed_fields": link["changed_fields"], "reason": link["reason"],
                       "before": copy.deepcopy(target),
                       "after": {"item_key": item_key, "text": item["text"], "type": item["type"],
                                 **{k: proposed.get(k) for k in ("status", "owner", "deadline")}},
                       "prior_quotes": [{"da_id": d, "quote": quotes[d]} for d in link["prior_evidence_da_ids"] if d in quotes],
                       "current_quotes": [{"da_id": d, "quote": quotes[d]} for d in link["current_evidence_da_ids"] if d in quotes]})
    return output
