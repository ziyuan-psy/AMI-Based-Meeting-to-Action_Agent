"""Focused Decision selection on the fixed inputs from decision_study.py; no new extraction.

PYTHONPATH=src python scripts/decision_review_study.py <v1-folder> <new-folder> [--score]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import decision_study as study
from mta import extract
from mta.control import RunControl, RunStopped
from mta.decision_audit import explain, score
from mta.evaluate import make_llm_matcher, prf
from mta.judge import JsonCache
from mta.observation import REPOSITORY, digest, runtime_identity, sha256, utc_now, write_json
from mta.store import Store
from mta.trace import StageTrace


def run_case(source, destination, mid, llm):
    folder = destination / mid
    folder.mkdir(parents=True, exist_ok=True)
    metadata = folder / "case.json"
    if metadata.exists() and study.load(metadata)["status"] == "COMPLETED":
        return
    candidates, before = study.load(source / mid / "candidates.json"), study.load(source / mid / "before.json")
    store = Store(mid, allow_prior=False)
    acts = store.meeting(mid).acts
    trace = StageTrace(folder / "review.stages.jsonl")
    control = RunControl(trace=trace)
    scoped = llm.scoped(control)
    data = {"status": "RUNNING", "meeting_id": mid, "candidate_sha256": digest(candidates),
            "input_sha256": sha256(study.config.TRANSCRIPTS / f"{mid}.txt")}
    write_json(metadata, data)
    try:
        control.enter("decision_review")
        items = extract._review_output(scoped, before["final"], candidates, acts, folder / "consolidate.json", trace)
        after = {**before, "final": items, "normalized": extract.normalize(items, store.participant_ids()),
                 "decision_review_events": trace.events}
        write_json(folder / "before.json", before, control)
        write_json(folder / "after.json", after, control)
        write_json(folder / "candidates.json", candidates, control)
        data["status"] = "COMPLETED"
    except RunStopped as error:
        data.update(status=error.failure["status"], failure=error.failure)
    except Exception as error:
        data.update(status="FAILED", error_type=type(error).__name__)
    finally:
        control.seal()
        data.update(runtime_control=control.snapshot(), usage=scoped.usage_snapshot())
        trace.finish(stage="study", event="finished", status=data["status"])
        write_json(metadata, data)
    print(mid, "focused review", data["status"], "calls", data["runtime_control"]["attempted_calls"], flush=True)
    if data["status"] != "COMPLETED":
        raise RuntimeError(f"{mid} focused review failed; inspect case.json")


def score_case(source, destination, mid, llm):
    folder = destination / mid
    if (folder / "evaluation.json").exists():
        return study.load(folder / "evaluation.json")
    previous = study.load(source / mid / "evaluation.json")
    gold_path = REPOSITORY / "revised_gold_output" / f"{mid}.json"
    if sha256(gold_path) != previous["gold_sha256"]:
        raise ValueError("Gold changed between study arms")
    gold = study.load(gold_path)
    trace = StageTrace(folder / "judge.stages.jsonl")
    control = RunControl(trace=trace)
    scoped = llm.scoped(control)
    result = {"meeting": mid, "gold_sha256": previous["gold_sha256"], "coverage": previous["coverage"],
              "arms": {"before": previous["arms"]["before"]}, "unscored_types": {}}
    try:
        matcher = make_llm_matcher(scoped, JsonCache.get(destination / "judge.align_votes.json"), votes=3)
        artifact = study.load(folder / "after.json")
        result["arms"]["after"] = score(artifact["normalized"], gold, matcher)
        result["arms"]["after"]["diagnosis"] = explain([g for g in gold if g["type"] == "decisions"],
            previous["coverage"], study.load(folder / "candidates.json"), artifact, result["arms"]["after"])
        for typ in ("actions", "decisions", "problems"):
            if any(g["type"] == typ and g["text"].strip().lower().rstrip(".") in ("na", "n/a") for g in gold):
                result["unscored_types"][typ] = "gold contains NA placeholder; entire meeting/type excluded in both arms"
        result.update(usage=scoped.usage_snapshot(), runtime_control=control.snapshot())
        write_json(folder / "evaluation.json", result)
        print(mid, "focused", result["arms"]["after"]["metrics"]["decisions"], flush=True)
        return result
    finally:
        control.seal()
        trace.finish(stage="study_judge", event="finished", runtime_control=control.snapshot())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--score", action="store_true")
    args = parser.parse_args(argv)
    study.prepare(args.destination)
    metadata = study.load(args.destination / "study.json")
    metadata.update(arms=["before", "after"], source_study=str(args.source.resolve()),
                    policy="one evidence-grounded Decision selection; legacy Action/Issue unchanged",
                    eligibility="same meeting/type excluded in both arms when gold contains NA placeholder")
    write_json(args.destination / "study.json", metadata)
    llm = study.LLM()
    for mid in study.MEETINGS:
        run_case(args.source, args.destination, mid, llm)
    if args.score:
        cache = args.destination / "judge.align_votes.json"
        if not cache.exists():
            shutil.copyfile(args.source / cache.name, cache)
        results = [score_case(args.source, args.destination, mid, llm) for mid in study.MEETINGS]
        aggregate = {arm: {} for arm in ("before", "after")}
        for arm in aggregate:
            for typ in ("actions", "decisions", "problems"):
                eligible = [r for r in results if typ not in r["unscored_types"]]
                counts = [sum(r["arms"][arm]["metrics"][typ][key] for r in eligible) for key in ("tp", "sys", "gold")]
                aggregate[arm][typ] = prf(*counts)
        write_json(args.destination / "comparison.json", {"aggregate": aggregate,
                   "unscored": {r["meeting"]: r["unscored_types"] for r in results if r["unscored_types"]}})
        print(json.dumps(aggregate), flush=True)
    metadata.update(status="COMPLETED" if args.score else "CAPTURED", completed_at=utc_now(),
                    artifact_sha256={p.relative_to(args.destination).as_posix(): sha256(p)
                        for p in sorted(args.destination.rglob("*")) if p.is_file() and p.name != "study.json"})
    write_json(args.destination / "study.json", metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
