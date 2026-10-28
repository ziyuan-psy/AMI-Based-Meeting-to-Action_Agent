"""Offline paired replay of captured live samples through the production Decision policy.

From agent/, PYTHONPATH=src:
python scripts/decision_study.py results/decision_studies/decision-v1 results/decision_studies/decision-v3
The destination must be new. Requires no API credentials; a missing cache is an error, never a model call.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

from mta import config, extract
from mta.decision_audit import as_records, explain
from mta.evaluate import prf
from mta.observation import REPOSITORY, digest, sha256, source_files, utc_now, write_json
from mta.store import Store
from mta.trace import StageTrace

MEETINGS = ("TS3006a", "TS3006b", "ES2011a", "IS1008a")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class CapturedOnly:
    def __init__(self, model):
        self.model = model

    def forced_tool(self, *args, **kwargs):
        raise AssertionError("Replay requested an uncaptured model response")


def migrate_key(candidates, model):
    """Label the legacy raw samples with the current lineage-cache format, without editing the source bundle."""
    listing = "\n".join(f"[{i}] ({c['type']}) {c['text']}"
        + (f" | owner cue: {c['owner_cue']}" if c.get("owner_cue") else "")
        + (f" | deadline: {c['deadline']}" if c.get("deadline") else "") for i, c in enumerate(candidates))
    return hashlib.sha256(("candidate-lineage-v1" + extract.CONSOLIDATE_SYSTEM + model + listing
        + (config.EFFORT_CONSOLIDATE or "") + json.dumps(config.MAX_ITEMS, sort_keys=True)).encode()).hexdigest()[:16]


def reuse_alignment(items, sources, source_scores):
    """The judge sees only the per-type text lists: reuse votes only after exact list equality is verified."""
    records, pairs, metrics = as_records(items), {}, {}
    for typ in ("actions", "decisions", "problems"):
        source_arm = "after" if typ == "decisions" else "before"
        previous = as_records(sources[source_arm]["normalized"])
        old = [i for i in previous if i["type"] == typ]
        new = [i for i in records if i["type"] == typ]
        if [i["text"] for i in old] != [i["text"] for i in new]:
            raise ValueError(f"{typ} text lists changed: fresh voted alignment is required")
        old_pairs = source_scores["arms"][source_arm]["pairs"]
        pairs.update({n["id"]: old_pairs[o["id"]] for o, n in zip(old, new) if o["id"] in old_pairs})
        metrics[typ] = source_scores["arms"][source_arm]["metrics"][typ]
    return {"metrics": metrics, "pairs": pairs}


def capture(source, destination):
    if destination.exists():
        raise FileExistsError("Choose a new replay destination")
    original = load(source / "study.json")
    if original.get("status") not in ("CAPTURED", "COMPLETED"):
        raise ValueError("Expected a captured live study")
    model = original["runtime"]["model"]["name"]
    for name in ("CONSOLIDATE_SAMPLES", "ACTION_SAMPLES", "MAX_ITEMS", "EFFORT_CONSOLIDATE"):
        if original["runtime"]["config"][name] != getattr(config, name):
            raise ValueError(f"Replay configuration differs: {name}")
    destination.mkdir(parents=True)
    shutil.copytree(source / "source_before", destination / "source_before",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for path in source_files():
        target = destination / "source_after" / path.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    results = []
    for mid in MEETINGS:
        case = destination / mid
        candidates = load(source / mid / "candidates.json")
        source_case = load(source / mid / "case.json")
        if sha256(config.TRANSCRIPTS / f"{mid}.txt") != source_case["input_sha256"]:
            raise ValueError(f"Input changed: {mid}")
        before = load(source / mid / "before.json")
        trial_after = load(source / mid / "after.json")
        scores = load(source / mid / "evaluation.json")
        gold_path = REPOSITORY / "revised_gold_output" / f"{mid}.json"
        if sha256(gold_path) != scores["gold_sha256"]:
            raise ValueError(f"Gold changed: {mid}")
        gold = load(gold_path)
        base_cache = dict(before["cache"], key=migrate_key(candidates, model), runs=before["runs"])
        cache = case / "consolidate.json"
        write_json(cache, base_cache)
        shutil.copyfile(source / mid / "after.cache.json", case / "consolidate.decisions.json")
        trace = StageTrace(case / "stages.jsonl")
        out = extract.consolidate(CapturedOnly(model), candidates, cache, trace, enable_decision_pass=True)
        normalized = extract.normalize(out, Store(mid, allow_prior=False).participant_ids())
        after = {"normalized": normalized, "final": out, "cache": load(case / "consolidate.decisions.json"),
                 "voted": extract.vote_items(load(case / "consolidate.decisions.json")["runs"], refine_decisions=True)}
        result = {"meeting": mid, "gold_sha256": scores["gold_sha256"], "coverage": scores["coverage"],
                  "arms": {"before": scores["arms"]["before"],
                           "after": reuse_alignment(normalized, {"before": before, "after": trial_after}, scores)},
                  "unscored_types": {}, "candidate_sha256": digest(candidates),
                  "scoring": "same three-vote judge; exact per-type text-list identity checked before reusing cached votes"}
        for typ in ("actions", "decisions", "problems"):
            if any(g["type"] == typ and g["text"].strip().lower().rstrip(".") in ("na", "n/a") for g in gold):
                result["unscored_types"][typ] = "NA placeholder: entire meeting/type excluded in both arms"
        decisions = [g for g in gold if g["type"] == "decisions"]
        result["arms"]["after"]["diagnosis"] = explain(decisions, scores["coverage"], candidates, after, result["arms"]["after"])
        results.append(result)
        for name, value in (("candidates", candidates), ("before", before), ("after", after), ("evaluation", result)):
            write_json(case / f"{name}.json", value)
        trace.finish(stage="study", event="finished", status="COMPLETED", model_calls_in_replay=0)
    aggregate = {arm: {} for arm in ("before", "after")}
    for arm in aggregate:
        for typ in ("actions", "decisions", "problems"):
            eligible = [r for r in results if typ not in r["unscored_types"]]
            counts = [sum(r["arms"][arm]["metrics"][typ][key] for r in eligible) for key in ("tp", "sys", "gold")]
            aggregate[arm][typ] = prf(*counts)
    write_json(destination / "comparison.json", {"meetings": MEETINGS, "aggregate": aggregate,
        "unscored": {r["meeting"]: r["unscored_types"] for r in results if r["unscored_types"]}})
    manifest = {"status": "COMPLETED", "execution_kind": "production_replay_of_captured_live_samples",
        "created_at": utc_now(), "source_study": str(source.resolve()), "source_study_sha256": sha256(source / "study.json"),
        "source_alignment_sha256": sha256(source / "judge.align_votes.json"), "meetings": MEETINGS,
        "base_commit": original["before_commit"], "captured_model": original["runtime"]["model"],
        "config": {**original["runtime"]["config"], "DECISION_RECONSOLIDATE": True},
        "protocol": "same candidates/gold/model/effort/type caps; one-to-one same-type three-vote semantic alignment",
        "scope": "development extraction/consolidation only; no live agent rerun or final evaluation",
        "fewshot_exclusions": [], "judgement": "model-voted; not independently human validated",
        "artifact_sha256": {p.relative_to(destination).as_posix(): sha256(p)
            for p in sorted(destination.rglob("*")) if p.is_file() and p.name != "study.json"
            and "__pycache__" not in p.parts and p.suffix != ".pyc"}}
    write_json(destination / "study.json", manifest)
    return aggregate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(capture(args.source, args.destination)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
