"""N5 follow-up agent: the model picks lookups; code enforces tools, budgets and validity."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from . import config
from .extract import VALID_STATUS
from .llm import LLM
from .prompts import AGENT_SYSTEM
from .store import Store
from .trace import Trace

RELATIONS = ["continues", "updates", "replaces", "cancels", "completes", "resolves", "reopens"]
BASIS = ["explicit", "inferred", "ambiguous", "missing"]
ALL_STATUS = sorted({s for v in VALID_STATUS.values() for s in v})


def tool_defs(allow_prior: bool) -> list[dict]:
    scopes = ["current", "prior"] if allow_prior else ["current"]
    tools = [
        {"name": "search_dialogue", "description": "Keyword search over dialogue lines.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"}, "scope": {"enum": scopes}, "k": {"type": "integer"}},
             "required": ["query", "scope"]}},
        {"name": "read_dialogue", "description": "Read dialogue lines around one line (window <= 15).",
         "input_schema": {"type": "object", "properties": {
             "meeting_id": {"type": "string"}, "da_id": {"type": "string"}, "window": {"type": "integer"}},
             "required": ["meeting_id", "da_id"]}},
        {"name": "get_participants", "description": "Speaker letters, participant IDs and roles of a meeting.",
         "input_schema": {"type": "object", "properties": {"meeting_id": {"type": "string"}},
                          "required": ["meeting_id"]}},
        {"name": "search_project_doc", "description": "Search the AMI scenario / project documents.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    ]
    if allow_prior:
        tools.append({"name": "get_prior_items",
                      "description": "Keyword search of earlier record TEXT in this series. Choose subject/property keywords, not a meeting ID. Returns candidate records and evidence locators, not the dialogue text.",
                      "input_schema": {"type": "object", "properties": {
                          "query": {"type": "string", "description": "Short content keywords from the item; meeting IDs are not indexed."},
                          "item_type": {"enum": ["actions", "decisions", "problems"]}},
                          "required": ["query"]}})
        tools.append({"name": "read_prior_item", "description": "Read a returned prior record and its actual supporting dialogue; use query to focus evidence or offset for more.",
                      "input_schema": {"type": "object", "properties": {
                          "item_id": {"type": "string"}, "query": {"type": "string"},
                          "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                          "window": {"type": "integer", "minimum": 0, "maximum": 2}}, "required": ["item_id"]}})
    tools.append({"name": "finish", "description": "Submit the final fields for this item.",
                  "input_schema": {"type": "object", "properties": {
                      "owner": {"type": ["array", "null"], "items": {"type": "string"}},
                      "owner_basis": {"enum": BASIS},
                      "deadline": {"type": ["string", "null"]},
                      "deadline_basis": {"enum": BASIS},
                      "status": {"enum": ALL_STATUS},
                      "prior_context_needed": {"type": "boolean"},
                      "related_items": {"type": "array", "items": {
                          "type": "object", "properties": {"id": {"type": "string"}, "relation": {"enum": RELATIONS}},
                          "required": ["id", "relation"]}},
                      "evidence_da_ids": {"type": "array", "items": {"type": "string"}},
                      "used_participant_lookup": {"type": "boolean"}},
                      "required": ["status", "prior_context_needed", "related_items", "evidence_da_ids"]}})
    return tools


@dataclass
class MeetingBudget:
    """Lookups shared by all items of one meeting. Items run in parallel threads, so a lookup is reserved under a lock."""
    used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def left(self) -> int:
        return config.MAX_CALLS_PER_MEETING - self.used

    def take(self) -> bool:
        with self._lock:
            if self.used >= config.MAX_CALLS_PER_MEETING:
                return False
            self.used += 1
            return True


def meeting_of(da_id: str) -> str:
    return da_id.split(".")[0]


def build_reference(store: Store, da_ids: list[str], owner_ids: list[str] | None,
                    use_meta: bool, pages: list[int]) -> list[dict]:
    refs, seen = [], set()

    def add(r):
        key = json.dumps(r, sort_keys=True)
        if key not in seen:
            seen.add(key)
            refs.append(r)

    for d in da_ids:
        mid = meeting_of(d)
        add({"source_type": "dialogue", "source": f"ami-corpus/dialogueActs/{mid}.json", "locator": d})
    if use_meta:
        for pid in owner_ids or []:
            loc = store.metadata_locator(store.current, pid)
            if loc:
                add({"source_type": "participant_metadata",
                     "source": f"ami-corpus/dialogueActs/{store.current}.json", "locator": loc})
    for p in pages:
        add({"source_type": "project_document", "source": "amiScenario.pdf", "locator": f"PDF page {p} (one-based)"})
    return refs


def check_finish(store: Store, item: dict, a: dict, pages_seen: set[int]) -> tuple[dict | None, str | None]:
    """Validate a `finish` payload against the item type and visibility rules."""
    typ = item["type"]
    missing = [k for k in ("status", "prior_context_needed", "related_items", "evidence_da_ids") if k not in a]
    if missing:
        return None, f"finish is missing required field(s): {missing}"
    a = {**a, "related_items": a.get("related_items") or [], "evidence_da_ids": a.get("evidence_da_ids") or []}
    if a["status"] not in VALID_STATUS[typ]:
        return None, f"status '{a['status']}' is not allowed for {typ}: {sorted(VALID_STATUS[typ])}"
    for d in a["evidence_da_ids"]:
        if not store.da_exists(meeting_of(d), d):
            return None, f"evidence line {d} does not exist or is not readable from {store.current}"
    if not a["evidence_da_ids"]:
        return None, "evidence_da_ids must not be empty"
    related = []
    for r in a["related_items"]:
        if not isinstance(r, dict) or r.get("id") is None or r.get("relation") not in RELATIONS:
            return None, "each related item needs an id and a relation from " + str(RELATIONS)
        if not store.prior_item_exists(r["id"]):
            return None, f"related item {r['id']} is not an item recorded for an earlier meeting; use ids from get_prior_items"
        related.append({"id": r["id"], "relation": r["relation"]})
    # Derived in code: an earlier meeting is needed only if the item links to an earlier item or its support comes from one.
    prior_needed = bool(related) or any(meeting_of(d) != store.current for d in a["evidence_da_ids"])
    out = {"status": a["status"], "prior_context_needed": prior_needed, "related_items": related}
    if typ == "actions":
        ob, db = a.get("owner_basis"), a.get("deadline_basis")
        if ob not in BASIS or db not in BASIS:
            return None, "actions need owner_basis and deadline_basis"
        owner = a.get("owner")
        if ob in ("explicit", "inferred"):
            if not owner or any(o not in store.participant_ids() for o in owner):
                return None, f"owner must list participant IDs of this meeting {sorted(store.participant_ids())}"
        else:
            owner = None
        dl = (a.get("deadline") or "").strip() or None
        if db in ("explicit", "inferred") and not dl:
            return None, "deadline_basis explicit/inferred needs the deadline wording"
        if db == "missing":
            dl = None
        out.update(owner=owner, owner_basis=ob, deadline=dl, deadline_basis=db)
    pages = sorted(p for p in pages_seen)
    out["reference"] = build_reference(store, a["evidence_da_ids"], out.get("owner"),
                                       bool(a.get("used_participant_lookup")), pages)
    return out, None


def _blocks(resp) -> list[dict]:
    out = []
    for b in resp.content:
        if b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif b.type == "text":
            out.append({"type": "text", "text": b.text})
    return out


def item_prompt(store: Store, item: dict) -> str:
    ev = []
    for d in item["evidence_da_ids"][:12]:
        found = store.meeting(meeting_of(d)).acts[store.meeting(meeting_of(d)).by_id[d]]
        ev.append(f"{d} | {found['attributes']['role']}({found['attributes']['participant']}): {found['text']}")
    info = {k: item.get(k) for k in ("type", "text", "flags", "owner_ids", "owner_basis", "owner_cue",
                                      "deadline", "deadline_basis", "deadline_cue", "status")}
    info["current_meeting"] = store.current
    info["earlier_meetings_available"] = store.visible_ids("prior")
    info["roster"] = store.roster(store.current)
    return ("Item to verify (fields are provisional):\n" + json.dumps(info, indent=1)
            + "\n\nEvidence lines found so far:\n" + "\n".join(ev))


def run_agent(llm: LLM, store: Store, item: dict, budget: MeetingBudget, trace: Trace, item_key: str,
              progress=None) -> dict | None:
    if config.P2_ENABLED:
        from .p2 import run_p2_agent
        return run_p2_agent(llm, store, item, budget, trace, item_key, progress)
    return _run_legacy_agent(llm, store, item, budget, trace, item_key)


def _run_legacy_agent(llm: LLM, store: Store, item: dict, budget: MeetingBudget, trace: Trace, item_key: str) -> dict | None:
    """Returns the final fields, or None when the agent could not produce a valid finish (caller falls back)."""
    system = AGENT_SYSTEM.replace("{max_calls}", str(config.MAX_CALLS_PER_ITEM))
    if not store.allow_prior:
        system += "\nEarlier meetings are NOT available in this run; only the current meeting can be searched.\n"
    tools = tool_defs(store.allow_prior)
    messages = [{"role": "user", "content": item_prompt(store, item)}]
    seen_ids = set(item["evidence_da_ids"])
    cache: dict[str, dict] = {}
    pages_seen: set[int] = set()
    calls = no_progress = invalid = finish_tries = step = 0

    while True:
        force = (calls >= config.MAX_CALLS_PER_ITEM or budget.left() <= 0
                 or no_progress >= config.MAX_NO_PROGRESS or invalid >= config.MAX_INVALID_ACTIONS)
        if force and finish_tries >= config.MAX_FINISH_TRIES:
            trace.log(item=item_key, step=step, flags=item["flags"], action=None, outcome="gave_up",
                      reason="no valid finish")
            return None
        if step >= config.MAX_AGENT_STEPS:
            trace.log(item=item_key, step=step, flags=item["flags"], action=None, outcome="gave_up",
                      reason="step limit")
            return None
        choice = {"type": "tool", "name": "finish"} if force else {"type": "any"}
        resp = llm.create(system, messages, tools, choice, effort=config.EFFORT_AGENT, stage="agent")
        blocks = _blocks(resp)
        # An empty reply would become an assistant message with null content, which the API rejects on the next call.
        messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no tool call)"}]})
        uses = [b for b in blocks if b["type"] == "tool_use"]
        step += 1
        if not uses:
            invalid += 1
            if force:
                finish_tries += 1  # a forced finish that returns no tool call still uses up a try
            trace.log(item=item_key, step=step, flags=item["flags"], action=None, outcome="no_tool_call", forced=force)
            messages.append({"role": "user", "content": "Call exactly one tool."})
            continue
        results = []
        for extra in uses[1:]:
            results.append({"type": "tool_result", "tool_use_id": extra["id"], "is_error": True,
                            "content": "One action per step; this call was ignored."})
        tu = uses[0]
        name, args = tu["name"], tu["input"]
        entry = {"item": item_key, "step": step, "flags": item["flags"], "action": name, "args": args,
                 "budget_left": {"item": config.MAX_CALLS_PER_ITEM - calls, "meeting": budget.left()}}
        if name == "finish":
            finish_tries += 1
            fields, err = check_finish(store, item, args, pages_seen)
            if fields:
                trace.log(**entry, outcome="finished", related=fields["related_items"])
                return fields
            invalid += 1
            trace.log(**entry, outcome="invalid_finish", error=err)
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "is_error": True, "content": err})
        else:
            allowed = {t["name"] for t in tools}
            if name not in allowed:
                invalid += 1
                trace.log(**entry, outcome="invalid_action")
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                                "content": f"Unknown or unavailable tool {name}."})
            elif calls >= config.MAX_CALLS_PER_ITEM:
                trace.log(**entry, outcome="item_budget_exhausted")
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                                "content": "This item's lookup budget is used up. Call finish now."})
            elif not budget.take():
                # other items used up the meeting's lookups while this step was in flight: finish next
                trace.log(**entry, outcome="budget_exhausted")
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                                "content": "The meeting's lookup budget is used up. Call finish now."})
            else:
                key = name + json.dumps(args, sort_keys=True)
                calls += 1
                error = None
                if key in cache:
                    data, new_info = {"note": "duplicate call; same result as before", **cache[key]}, False
                else:
                    try:
                        data = _execute(store, name, args)
                        ids = _ids_in(name, data)
                        new_info = bool(ids - seen_ids)
                        seen_ids |= ids
                        if name == "search_project_doc":
                            pages_seen |= {r["page"] for r in data["results"]}
                        cache[key] = data
                    except (PermissionError, KeyError, ValueError, TypeError) as e:
                        invalid += 1
                        error = f"{type(e).__name__}: {e}"
                        data, new_info = {"error": str(e)}, False
                no_progress = 0 if new_info else no_progress + 1
                if error:
                    trace.log(**entry, outcome="tool_error", error=error)
                else:
                    trace.log(**entry, outcome="ok", new_info=new_info, n_results=len(data.get("results", [])),
                              observation_ids=sorted(_ids_in(name, data))[:12])
                results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                "content": json.dumps(data)[:6000], "is_error": "error" in data})
        messages.append({"role": "user", "content": results})


def _execute(store: Store, name: str, a: dict) -> dict:
    if name == "search_dialogue":
        return {"results": store.search_dialogue(a["query"], a.get("scope", "current"), min(int(a.get("k", 5)), 8))}
    if name == "read_dialogue":
        return {"results": store.read_dialogue(a["meeting_id"], a["da_id"], a.get("window", 8))}
    if name == "get_participants":
        return {"results": store.roster(a["meeting_id"])}
    if name == "get_prior_items":
        return {"results": store.get_prior_items(a["query"], a.get("item_type"), 3)}
    if name == "read_prior_item":
        return {"results": store.read_prior_item(a["item_id"], a.get("query", ""), a.get("offset", 0), a.get("limit", 8), a.get("window", 1))}
    if name == "search_project_doc":
        return {"results": store.search_project_doc(a["query"], 3)}
    raise ValueError(f"unknown tool {name}")


def _ids_in(name: str, data: dict) -> set:
    res = data.get("results", [])
    if name in ("search_dialogue", "read_dialogue"):
        return {r["da_id"] for r in res}
    if name == "get_prior_items":
        return {r["id"] for r in res}
    if name == "read_prior_item":
        return {r.get("id", r.get("da_id")) for r in res}
    if name == "search_project_doc":
        return {f"p{r['page']}" for r in res}
    if name == "get_participants":
        return {r["participant_id"] for r in res}
    return set()
