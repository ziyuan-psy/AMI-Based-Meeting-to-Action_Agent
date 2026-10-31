"""Small live P2 protocol smoke test on saved TS3006a development items; no gold or final evaluation.

PYTHONPATH=src python scripts/p2_smoke.py <new-output-folder>
Uses one saved Action and one saved Decision. All turns share one meeting controller and its normal limits.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
from uuid import uuid4

from mta import config
from mta.agent import MeetingBudget, run_agent
from mta.baseline import check_destination
from mta.control import RunControl, RunStopped
from mta.llm import LLM
from mta.observation import REPOSITORY, runtime_identity, sha256, source_files, utc_now, write_json
from mta.store import Store
from mta.trace import StageTrace, Trace
from mta.triage import flag_items


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--types", choices=("actions", "decisions"), nargs="+", default=["actions", "decisions"])
    args = parser.parse_args(argv)
    check_destination(args.destination)
    if not config.P2_ENABLED:
        raise ValueError("P2 must be enabled for this smoke test")
    source = config.ROOT / "results/decision_studies/decision-v3/TS3006a/after.json"
    candidates = json.loads(source.read_text(encoding="utf-8"))["normalized"]
    selected = [copy.deepcopy(next(i for i in candidates if i["type"] == t)) for t in args.types]
    llm = LLM()
    args.destination.mkdir(parents=True)
    data = {"format_version": 1, "status": "RUNNING", "execution_kind": "live_p2_protocol_smoke",
            "run_id": uuid4().hex, "created_at": utc_now(), "meeting_id": "TS3006a",
            "scope": "saved development items; no extraction rerun, gold access, scoring or final evaluation",
            "selected_types": args.types,
            "source_items_sha256": sha256(source),
            "transcript_sha256": sha256(config.TRANSCRIPTS / "TS3006a.txt"), **runtime_identity(llm), "items": []}
    for path in source_files() + [config.P2_SCHEMA]:
        target = args.destination / "source" / path.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    write_json(args.destination / "inputs.json", selected)
    write_json(args.destination / "run.json", data)
    stages, trace = StageTrace(args.destination / "stages.jsonl"), Trace(args.destination / "agent.trace.jsonl")
    control, budget = RunControl(trace=stages), MeetingBudget()
    scoped = llm.scoped(control)
    store = Store("TS3006a", allow_prior=False)
    flag_items(selected, store)
    try:
        for n, item in enumerate(selected):
            item["run_id"] = data["run_id"]
            control.enter("p2_item_" + str(n))
            result = run_agent(scoped, store, item, budget, trace, "TS3006a#" + str(n), progress=lambda outcome, n=n:
                write_json(args.destination / f"item_{n}.checkpoint.json", outcome, control))
            data["items"].append(result)
            write_json(args.destination / "run.json", data, control)
            print(item["type"], result["state"], result["termination_reason"], flush=True)
        data["status"] = "COMPLETED"
    except RunStopped as error:
        data.update(status=error.failure["status"], failure=error.failure)
    except Exception as error:
        data.update(status="FAILED", error_type=type(error).__name__)
    finally:
        control.seal(); trace.close()
        data.update(runtime_control=control.snapshot(), usage=scoped.usage_snapshot(), finished_at=utc_now())
        stages.finish(stage="smoke", event="finished", status=data["status"])
        data["artifact_sha256"] = {p.relative_to(args.destination).as_posix(): sha256(p)
            for p in sorted(args.destination.rglob("*")) if p.is_file() and p.name != "run.json"}
        write_json(args.destination / "run.json", data)
    return 0 if data["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
