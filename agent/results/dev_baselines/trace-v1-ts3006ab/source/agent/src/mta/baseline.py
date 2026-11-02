"""Capture a small, immutable SB development run with its actual source and settings.

python -m mta.baseline --out results/dev_baselines/trace-v1 --meetings TS3006a TS3006b --modes s1 s2
No gold labels or evaluation judges are used. Existing destinations are refused.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from . import config
from .llm import LLM, OpenAIAdapter
from .observation import REPOSITORY, runtime_identity, sha256, source_files, utc_now, write_json
from .pipeline import MODES, run_meeting
from .store import split_meeting_id

DEVELOPMENT_SERIES = {"ES2011", "IS1008", "TS3004", "TS3006"}


def check_selection(meetings: list[str], modes: list[str]) -> list[str]:
    if not meetings or len(set(meetings)) != len(meetings):
        raise ValueError("Choose a nonempty list of unique development meetings")
    if not modes or len(set(modes)) != len(modes) or any(m not in MODES for m in modes):
        raise ValueError("Choose unique modes from b0, s1 and s2")
    for meeting in meetings:
        series, order = split_meeting_id(meeting)
        if series not in DEVELOPMENT_SERIES:
            raise ValueError("Development baselines accept SB series only; held-out series are refused")
        if "s2" in modes and any(f"{series}{letter}" not in meetings for letter in "abcd"[:order - 1]):
            raise ValueError(f"Include all earlier meetings of {series} for an S2 baseline")
        if not (config.TRANSCRIPTS / f"{meeting}.txt").is_file():
            raise ValueError(f"Missing development transcript: {meeting}")
    return sorted(meetings, key=split_meeting_id)


def check_destination(destination: Path) -> None:
    resolved = destination.resolve()
    protected = [REPOSITORY / name for name in (".git", "revised_gold_output", "transcripts", "scripts", "tests", "schemas")]
    protected += [config.DATA, config.ROOT / "src", config.ROOT / "scripts", config.ROOT / "tests",
                  REPOSITORY.parent / "ami-corpus"]
    if any(resolved.is_relative_to(p.resolve()) for p in protected):
        raise ValueError("Baseline destination must not be inside source, input data or annotations")
    if destination.exists():
        raise FileExistsError("Baseline destination already exists; choose a new name")


def inventory(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): sha256(p) for p in sorted(folder.rglob("*"))
            if p.is_file() and p.name != "baseline.json"}


def render_summary(folder: Path, manifest: dict) -> str:
    runtime = manifest["runtime"]
    lines = ["# Development baseline", "", f"Status: {manifest['status']}",
             f"Execution: {manifest['execution_kind']}", f"Model: `{runtime['model']['name']}`",
             f"Base commit: `{runtime['code']['git_commit']}`",
             f"Source digest: `{runtime['code']['source_digest']}`", "",
             "The source/ snapshot identifies the actual executed source, including uncommitted changes.",
             "This is a development capture, with no gold scoring or held-out data.", "",
             "Cache policy: cold at capture start; S1/S2 share extraction/consolidation caches within this bundle.",
             "S2 timing therefore includes cache reuse. It is not a cold end-to-end latency comparison.", "",
             "| Mode | Meeting | Status | Candidates A/D/P | Final A/D/P | Calls | Tokens (in/out) | Seconds |",
             "|---|---|---|---|---|---|---|---|"]
    counts = lambda c: "/".join(str(c.get(t, 0)) for t in ("actions", "decisions", "problems"))
    for run in manifest["runs"]:
        result = run["result"]
        stem = folder / run["mode"] / run["meeting_id"]
        stage_file = stem.with_suffix(".stages.jsonl")
        events = [json.loads(line) for line in stage_file.read_text(encoding="utf-8").splitlines()] if stage_file.exists() else []
        extraction = next((e["counts"] for e in events if e["stage"] == "extract" and e["event"] == "completed"), {})
        record = stem.with_suffix(".json")
        final = Counter(i["type"] for i in json.loads(record.read_text(encoding="utf-8"))) if record.exists() else {}
        lines.append(f"| {run['mode']} | {run['meeting_id']} | {result['status']} | {counts(extraction)} | {counts(final)} | "
                     f"{result.get('llm calls', '-')} | {result.get('input tokens', '-')}/{result.get('output tokens', '-')} | "
                     f"{result.get('seconds', '-')} |")
    lines += ["", "Per meeting, inspect `<mode>/<meeting>.stages.jsonl` for raw extraction attempts, candidate indexes,",
              "each consolidation attempt (raw groups, merged items and per-sample caps), majority-vote clusters,",
              "restored actions and final caps. `.run.json` records code/model/config, input/context hashes and usage.",
              "The existing `.trace.jsonl` remains the agent tool-decision log.", "",
              "`baseline.json` includes SHA-256 hashes for every saved artifact and source/input snapshot.",
              "API keys and .env files are never part of the capture."]
    return "\n".join(lines) + "\n"


def capture(destination: Path, meetings: list[str], modes: list[str], llm=None) -> dict:
    meetings = check_selection(meetings, modes)
    check_destination(destination)
    llm = llm if llm is not None else LLM()
    destination.mkdir(parents=True, exist_ok=False)
    runtime = runtime_identity(llm)
    live = isinstance(llm.client, OpenAIAdapter) or type(llm.client).__module__.startswith("anthropic")
    manifest = {"format_version": 1, "status": "RUNNING", "created_at": utc_now(),
                "execution_kind": "live_model" if live else "injected_client_test_fixture",
                "meetings": meetings, "modes": modes, "runtime": runtime,
                "cache_policy": "cold_then_shared_within_bundle", "runs": []}
    metadata_path = destination / "baseline.json"
    # Save only runtime code and approved input/context files. Never copy the worktree or .env wholesale.
    inputs = [config.TRANSCRIPTS / f"{m}.txt" for m in meetings]
    for path in sorted(set(source_files() + inputs + [config.PARTICIPANTS, config.SCENARIO, config.SCHEMA])):
        target = destination / "source" / path.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    write_json(metadata_path, manifest)
    try:
        for mode in modes:
            for meeting in meetings:
                print(f"Starting {mode} {meeting}", flush=True)
                result = run_meeting(config.TRANSCRIPTS / f"{meeting}.txt", mode, llm, destination,
                                     cache_dir=destination / "_cache")
                manifest["runs"].append({"mode": mode, "meeting_id": meeting, "result": result})
                write_json(metadata_path, manifest)
                print(f"Finished {mode} {meeting}: {result['status']}", flush=True)
                if result["status"] != "COMPLETED":
                    manifest["status"] = "BLOCKED"
                    return manifest
        manifest["status"] = "COMPLETED"
        return manifest
    except Exception as error:
        manifest.update(status="ERROR", error_type=type(error).__name__)
        raise
    finally:
        manifest["finished_at"] = utc_now()
        (destination / "BASELINE.md").write_text(render_summary(destination, manifest), encoding="utf-8")
        manifest["artifact_sha256"] = inventory(destination)
        write_json(metadata_path, manifest)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--meetings", nargs="+", required=True)
    ap.add_argument("--modes", choices=MODES, nargs="+", default=["s1", "s2"])
    args = ap.parse_args(argv)
    try:
        result = capture(args.out, args.meetings, args.modes)
    except Exception as error:
        # Remote SDK exceptions may contain request details; keep the console summary credential-free.
        print(f"Baseline capture failed: {type(error).__name__}. Inspect the saved run metadata.", flush=True)
        return 1
    print(f"Baseline {result['status']}: {args.out / 'BASELINE.md'}", flush=True)
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
