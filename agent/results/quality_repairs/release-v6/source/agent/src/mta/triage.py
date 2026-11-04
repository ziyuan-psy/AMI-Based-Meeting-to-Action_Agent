"""N4 triage: decide which items need the follow-up agent, and why."""
from __future__ import annotations

import re

from . import config
from .store import Store, history_tokens, tokenize

CONTENT_STOP = set("the a an of to and in for on will be is are with by that this it as at from or we they their "
                   "should would can have has not no use design remote control user project meeting".split())


def content(text: str) -> set[str]:
    return {w for w in tokenize(text) if w not in CONTENT_STOP and len(w) > 2}


def best_overlap(text: str, prior: list[dict]) -> float:
    def normalized(s):
        return {w for w in history_tokens(s) if w not in CONTENT_STOP and len(w) > 2}
    a = normalized(text)
    return max((len(a & normalized(p["text"])) / len(a | normalized(p["text"]))
                for p in prior if a | normalized(p["text"])), default=0.0)

STATUS_WORDS = re.compile(
    r"\b(done|finished|completed|already|dropp?ed?|cancel\w*|instead|no longer|rather than|changed?|"
    r"scrap\w*|postpone\w*|decided against|not going to|continu(?:e|es|ed|ing)|previous(?:ly)?|earlier|"
    r"still|as before|last (?:time|meeting)|same (?:task|plan|choice|design|approach))\b", re.I)


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
            else:  # missing system history is not evidence that no relationship exists
                flags += ["POSSIBLE_PRIOR", "PRIOR_RECORDS_UNAVAILABLE"]
        it["flags"] = flags
