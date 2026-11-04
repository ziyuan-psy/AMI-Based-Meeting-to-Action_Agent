"""Paired development study: same extracted candidates, legacy replay, code replay, fresh consolidation.

From agent/, PYTHONPATH=src: python scripts/decision_study.py <new-directory> [--score]
No held-out data. Gold is read only when --score is requested, after capture. Does not run the agent/evaluation suite.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from mta import config, extract
from mta.control import RunControl, RunStopped
from mta.decision_audit import as_records, candidate_coverage, explain, hydrate_runs, replay, score
from mta.evaluate import MATCH_SYSTEM, make_llm_matcher, prf
from mta.judge import JsonCache
from mta.llm import LLM
from mta.observation import REPOSITORY, digest, runtime_identity, sha256, source_files, utc_now, write_json
from mta.store import Store
from mta.trace import StageTrace

MEETINGS = ("TS3006a", "TS3006b", "ES2011a", "IS1008a")  # no prompt-example items in these meetings
FROZEN = config.ROOT / "results/dev_baselines/trace-v1-ts3006ab"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def legacy(destination):
    folder = destination / "source_before"
    for name in ("prompts", "extract"):
        spec = importlib.util.spec_from_file_location(f"mta._study_before_{name}", folder / f"agent/src/mta/{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if name == "prompts":
            prompts = module
    module.CONSOLIDATE_SYSTEM, module.EXTRACT_SYSTEM = prompts.CONSOLIDATE_SYSTEM, prompts.EXTRACT_SYSTEM
    return module


def prepare(destination):
    if destination.exists():
        return
    destination.mkdir(parents=True)
    git = ["git", "-c", f"safe.directory={REPOSITORY.as_posix()}"]
    before_commit = subprocess.check_output(git + ["rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    for path in source_files():
        relative = path.relative_to(REPOSITORY)
        after = destination / "source_after" / relative
        after.parent.mkdir(parents=True, exist_ok=True)
        after.write_bytes(path.read_bytes())
        saved = subprocess.run(git + ["show", f"HEAD:{relative.as_posix()}"], cwd=REPOSITORY, capture_output=True)
        if saved.returncode == 0:
            before = destination / "source_before" / relative
            before.parent.mkdir(parents=True, exist_ok=True)
            before.write_bytes(saved.stdout)
    write_json(destination / "study.json", {"status": "RUNNING", "created_at": utc_now(), "before_commit": before_commit,
        "meetings": MEETINGS, "arms": ["before", "replay", "after"],
        "scope": "development extraction/consolidation only; fixed candidate inputs; not final evaluation",
        "selection": "two existing trace cases for diagnosis, two additional series for development validation",
        "protocol": "same model, effort, 3 consolidation samples, type caps; 3-vote one-to-one semantic scoring",
        "judgement": "model-voted; not independently human validated", "fewshot_exclusions": [],
        "runtime": runtime_identity(LLM())})


def capture_case(destination, mid, llm, old):
    folder = destination / mid
    metadata = folder / "case.json"
    if metadata.exists() and load(metadata)["status"] == "COMPLETED":
        print(mid, "capture already saved", flush=True)
        return
    folder.mkdir(parents=True, exist_ok=True)
    trace = StageTrace(folder / "capture.stages.jsonl")
    control = RunControl(trace=trace)
    scoped = llm.scoped(control)
    data = {"status": "RUNNING", "meeting_id": mid, "input_sha256": sha256(config.TRANSCRIPTS / f"{mid}.txt")}
    write_json(metadata, data)
    try:
        control.enter("extract")
        if mid.startswith("TS3006"):
            frozen_events = [json.loads(line) for line in (FROZEN / "s1" / f"{mid}.stages.jsonl").read_text(encoding="utf-8").splitlines()]
            candidates = next(e["candidates"] for e in frozen_events if e["stage"] == "extract" and e["event"] == "completed")
            before_cache = load(FROZEN / "_cache" / f"consolidate_{mid}.json")
            data["candidate_source"] = "frozen_live_trace-v1-ts3006ab"
        else:
            store = Store(mid, allow_prior=False)
            current = store.meeting(mid)
            candidates = old.extract_candidates(scoped, current.acts, store.roster(mid), cache=folder / "extract.cache.json", trace=trace)
            control.enter("consolidate_before")
            old.consolidate(scoped, candidates, cache=folder / "before.cache.json", trace=trace)
            before_cache = load(folder / "before.cache.json")
            data["candidate_source"] = "live_legacy_extraction"
        write_json(folder / "candidates.json", candidates, control)
        data["candidate_sha256"] = digest(candidates)
        control.enter("replay")
        before = replay(before_cache, candidates, old)
        # Old items predate member annotations; hydrate for diagnostics without changing the legacy votes.
        hydrated = hydrate_runs(before_cache)
        for items in [*before["runs"], before["voted"], before["final"]]:
            for item in items:
                found = next((x for run in hydrated for x in run if x["text"] == item["text"] and x["type"] == item["type"]), None)
                if found:
                    item["candidate_indexes"] = found["candidate_indexes"]
        for sample, attempts in enumerate(before_cache["run_traces"]):
            merged = next(e for e in attempts[-1]["events"] if e["event"] == "merged")
            for item, lineage in zip(merged["items"], merged["lineage"]):
                item["candidate_indexes"] = lineage["candidate_indexes"]
            cap = next(e for e in attempts[-1]["events"] if e["event"] == "cap")
            for item in cap["kept"]:
                found = next(x for x in hydrated[sample] if x["text"] == item["text"] and x["type"] == item["type"])
                item["candidate_indexes"] = found["candidate_indexes"]
        before["cache"] = before_cache
        code = replay(before_cache, candidates, extract)
        # Replay stores the real raw inputs and fresh merge/cap lineage for audit.
        replay_cache = {"runs": code["runs"], "run_traces": []}
        for sample, attempts in enumerate(before_cache["run_traces"]):
            groups = next(e["groups"] for e in attempts[-1]["events"] if e["event"] == "raw_groups")
            detail = StageTrace()
            extract._merge_groups(groups, candidates, detail)
            replay_cache["run_traces"].append([{"events": detail.events}])
        code["cache"] = replay_cache
        control.enter("consolidate_after")
        extract.consolidate(scoped, candidates, cache=folder / "after.cache.json", trace=trace)
        after_cache = load(folder / "after.cache.json")
        after = replay(after_cache, candidates, extract)
        after["cache"] = after_cache
        valid_ids = Store(mid, allow_prior=False).participant_ids()
        for arm, result in (("before", before), ("replay", code), ("after", after)):
            result["normalized"] = extract.normalize(result["final"], valid_ids)
            write_json(folder / f"{arm}.json", result, control)
        data.update(status="COMPLETED", before_prompt_sha256=digest(old.CONSOLIDATE_SYSTEM),
                    after_prompt_sha256=digest(extract.CONSOLIDATE_SYSTEM))
    except RunStopped as error:
        data.update(status=error.failure["status"], failure=error.failure)
    except Exception as error:
        data.update(status="FAILED", error_type=type(error).__name__)
    finally:
        control.seal()
        data["runtime_control"], data["usage"] = control.snapshot(), scoped.usage_snapshot()
        trace.finish(stage="study", status=data["status"])
        write_json(metadata, data)
    print(mid, "capture", data["status"], "calls", data["runtime_control"]["attempted_calls"], flush=True)
    if data["status"] != "COMPLETED":
        raise RuntimeError(f"{mid} capture failed; inspect case.json")


def score_case(destination, mid, llm):
    folder = destination / mid
    if (folder / "evaluation.json").exists():
        return load(folder / "evaluation.json")
    trace = StageTrace(folder / "judge.stages.jsonl")
    control = RunControl(trace=trace)
    scoped = llm.scoped(control)
    try:
        matcher = make_llm_matcher(scoped, JsonCache.get(destination / "judge.align_votes.json"), votes=3)
        gold_path = REPOSITORY / "revised_gold_output" / f"{mid}.json"
        gold = load(gold_path)
        candidates = load(folder / "candidates.json")
        decisions = [g for g in gold if g["type"] == "decisions"]
        coverage = candidate_coverage(candidates, decisions, scoped)
        result = {"meeting": mid, "gold_sha256": sha256(gold_path), "coverage": coverage,
                  "matcher_prompt_sha256": digest(MATCH_SYSTEM), "arms": {}}
        for arm in ("before", "replay", "after"):
            artifact = load(folder / f"{arm}.json")
            scored = score(artifact["normalized"], gold, matcher)
            scored["diagnosis"] = explain(decisions, coverage, candidates, artifact, scored)
            result["arms"][arm] = scored
            print(mid, arm, scored["metrics"]["decisions"], flush=True)
        result["runtime_control"], result["usage"] = control.snapshot(), scoped.usage_snapshot()
        write_json(folder / "evaluation.json", result)
        return result
    finally:
        control.seal()
        trace.finish(stage="study_judge", event="finished", runtime_control=control.snapshot())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--meetings", nargs="+", choices=MEETINGS, default=list(MEETINGS))
    args = parser.parse_args(argv)
    prepare(args.destination)
    old, llm = legacy(args.destination), LLM()
    for mid in args.meetings:
        capture_case(args.destination, mid, llm, old)
    if args.score:
        results = [score_case(args.destination, mid, llm) for mid in args.meetings]
        aggregate = {}
        for arm in ("before", "replay", "after"):
            aggregate[arm] = {}
            for typ in ("actions", "decisions", "problems"):
                counts = [sum(r["arms"][arm]["metrics"][typ][key] for r in results) for key in ("tp", "sys", "gold")]
                aggregate[arm][typ] = prf(*counts)
        write_json(args.destination / "comparison.json", {"meetings": args.meetings, "aggregate": aggregate})
        print(json.dumps(aggregate), flush=True)
    study = load(args.destination / "study.json")
    study.update(status="COMPLETED" if args.score else "CAPTURED", completed_at=utc_now(),
                 artifact_sha256={p.relative_to(args.destination).as_posix(): sha256(p)
                    for p in sorted(args.destination.rglob("*")) if p.is_file() and p.name != "study.json"})
    write_json(args.destination / "study.json", study)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
