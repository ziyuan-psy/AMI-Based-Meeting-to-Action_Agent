"""N8 validation (schema + rules) and the deterministic repair used before BLOCKED."""
from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator

from . import config
from .store import Store, split_meeting_id

_validator = None


def validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        _validator = Draft202012Validator(json.loads(config.SCHEMA.read_text(encoding="utf-8")))
    return _validator


def _ref_ok(store: Store, ref: dict) -> bool:
    st, loc = ref["source_type"], ref["locator"]
    if st in ("dialogue", "participant_metadata"):
        da = loc.split(" ")[0]
        try:
            return store.da_exists(da.split(".")[0], da)
        except (ValueError, KeyError):
            return False
    if st == "project_document":
        m = re.match(r"PDF page (\d+)", loc)
        return bool(m) and any(p == int(m.group(1)) for p, _ in store._scenario[0])
    return False


def check(items: list[dict], store: Store) -> list[str]:
    """Return human-readable errors; empty list means valid."""
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
              for e in validator().iter_errors(items)]
    prior_ids = {i["id"] for i in store.prior_items()}
    for it in items:
        iid = it["id"]
        for ref in it["reference"]:
            if not _ref_ok(store, ref):
                errors.append(f"{iid}: reference not found or not readable: {ref['locator']}")
        for rel in it.get("related_items", []):
            if rel["id"] not in prior_ids:
                errors.append(f"{iid}: related item {rel['id']} is not an earlier recorded item")
        if it["type"] == "actions":
            if it["owner_basis"] in ("explicit", "inferred") and not it["owner"]:
                errors.append(f"{iid}: owner_basis {it['owner_basis']} needs an owner")
            if it["deadline_basis"] in ("explicit", "inferred") and not it["deadline"]:
                errors.append(f"{iid}: deadline_basis {it['deadline_basis']} needs a deadline")
    return errors


def repair(items: list[dict], store: Store) -> list[str]:
    """In-place, rule-based fixes for the common slips. Returns a log of what was changed."""
    log = []
    prior_ids = {i["id"] for i in store.prior_items()}
    ids = store.participant_ids()
    for it in items:
        good = [r for r in it["reference"] if _ref_ok(store, r)]
        if len(good) != len(it["reference"]):
            log.append(f"{it['id']}: dropped {len(it['reference']) - len(good)} unreadable reference(s)")
            it["reference"] = good
        if "related_items" in it:
            keep = [r for r in it["related_items"] if r["id"] in prior_ids]
            if len(keep) != len(it["related_items"]):
                log.append(f"{it['id']}: dropped unknown related item(s)")
                it["related_items"] = keep
        if it["type"] == "actions":
            if it["owner"] and any(o not in ids for o in it["owner"]):
                it["owner"], it["owner_basis"] = None, "missing"
                log.append(f"{it['id']}: owner not in roster -> missing")
            if it["owner_basis"] in ("explicit", "inferred") and not it["owner"]:
                it["owner_basis"] = "missing"
            if it["owner_basis"] in ("ambiguous", "missing"):
                it["owner"] = None
            if it["deadline_basis"] in ("explicit", "inferred") and not it["deadline"]:
                it["deadline_basis"] = "missing"
            if it["deadline_basis"] == "missing":
                it["deadline"] = None
    return log
