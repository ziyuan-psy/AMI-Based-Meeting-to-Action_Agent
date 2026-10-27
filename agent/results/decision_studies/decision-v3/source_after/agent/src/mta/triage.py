"""N4 triage: decide which items need the follow-up agent, and why."""
from __future__ import annotations

import re

from . import config
from .store import Store, tokenize

CONTENT_STOP = set("the a an of to and in for on will be is are with by that this it as at from or we they their "
                   "should would can have has not no use design remote control user project meeting".split())


def content(text: str) -> set[str]:
    return {w for w in tokenize(text) if w not in CONTENT_STOP and len(w) > 2}


def best_overlap(text: str, prior: list[dict]) -> float:
    a = content(text)
    return max((len(a & content(p["text"])) / len(a | content(p["text"])) for p in prior if a | content(p["text"])), default=0.0)

STATUS_WORDS = re.compile(
    r"\b(done|finished|completed|already|dropp?ed?|cancel\w*|instead|no longer|rather than|changed?|"
    r"scrap\w*|postpone\w*|decided against|not going to)\b", re.I)


def flag_items(items: list[dict], store: Store) -> None:
    prior_items = store.prior_items()
    for it in items:
        flags = []
        if it["type"] == "actions":
            if it["owner_basis"] != "explicit":
                flags.append("OWNER_UNRESOLVED")
            if len(it["owner_ids"]) > 1:
                flags.append("JOINT_OWNER_CHECK")
            if it["deadline_basis"] in ("ambiguous", "inferred"):
                flags.append("DEADLINE_CHECK")
        if STATUS_WORDS.search(it["text"]) and store.order > 1:
            flags.append("STATUS_CHECK")
        if len(it["evidence_da_ids"]) < 1:
            flags.append("WEAK_EVIDENCE")
        if store.allow_prior and store.order > 1:
            if prior_items:
                if best_overlap(it["text"], prior_items) >= config.PRIOR_JACCARD:
                    flags.append("POSSIBLE_PRIOR")
            elif it["type"] == "actions":  # no earlier system output available: let the agent check the dialogue
                flags.append("POSSIBLE_PRIOR")
        it["flags"] = flags
