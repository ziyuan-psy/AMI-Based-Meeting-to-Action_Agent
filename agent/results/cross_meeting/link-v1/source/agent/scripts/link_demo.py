"""Focused live S2 development case using genuine saved inputs, without gold or fresh extraction.

PYTHONPATH=src python scripts/link_demo.py <new-output-folder> [--without-records]
The current input is a saved MVP Decision; history is the complete saved TS3006a baseline.
No expected target ID, relation or search query is supplied to the Agent.
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
from mta.p2 import validate_outputs
from mta.pipeline import make_record
from mta.render import render_report
from mta.store import Store
from mta.trace import StageTrace, Trace
from mta.triage import flag_items
from mta.validate import check


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--without-records", action="store_true", help="Missing-record ablation; earlier dialogue remains visible")
    args = parser.parse_args(argv)
    check_destination(args.destination)
    if not config.P2_ENABLED:
        raise ValueError("This case requires P2")
    old = config.ROOT / "results/dev_baselines/trace-v1-ts3006ab/s2/TS3006a.json"
    current = config.ROOT / "results/mvp/s2/TS3006b.json"
    source_item = next(r for r in json.loads(current.read_text(encoding="utf-8")) if r["id"] == "TS3006b.sys.s.11")
    item = {"type": source_item["type"], "text": source_item["text"], "status": source_item["status"],
            "evidence_da_ids": [r["locator"].split(" ")[0] for r in source_item["reference"] if r["source_type"] == "dialogue"],
            "owner_ids": [], "owner_basis": "missing", "deadline": None, "deadline_basis": "missing"}
    llm = LLM()
    args.destination.mkdir(parents=True)
    (args.destination / ".gitattributes").write_text("* -text -whitespace\n", encoding="utf-8")
    prior = args.destination / "prior_records"
    prior.mkdir()
    if not args.without_records:
        shutil.copyfile(old, prior / old.name)
    data = {"format_version": 1, "status": "RUNNING", "execution_kind": "live_cross_meeting_agent_stage",
            "run_id": uuid4().hex, "created_at": utc_now(), "meeting_id": "TS3006b",
            "scope": "One genuine saved MVP item + complete saved baseline history; no extraction rerun, gold or quality judge",
            "without_records": args.without_records, "human_review": {"status": "pending"},
            "inputs": {"prior_source": old.relative_to(REPOSITORY).as_posix(), "prior_sha256": sha256(old),
                       "current_source": current.relative_to(REPOSITORY).as_posix(), "current_sha256": sha256(current),
                       "source_item_id": source_item["id"],
                       "transcripts": {m: sha256(config.TRANSCRIPTS / (m + ".txt")) for m in ("TS3006a", "TS3006b")}},
            **runtime_identity(llm)}
    for path in source_files() + [config.SCHEMA, config.P2_SCHEMA, config.PARTICIPANTS]:
        target = args.destination / "source" / path.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    write_json(args.destination / "input.json", {"source_record": source_item, "agent_input": item})
    write_json(args.destination / "run.json", data)
    stages, trace = StageTrace(args.destination / "stages.jsonl"), Trace(args.destination / "agent.trace.jsonl")
    control, budget = RunControl(trace=stages), MeetingBudget()
    scoped = llm.scoped(control)
    store = Store("TS3006b", allow_prior=True, run_dir=prior)
    flag_items([item], store)
    item["run_id"] = data["run_id"]
    try:
        control.enter("cross_meeting_agent")
        result = run_agent(scoped, store, item, budget, trace, "TS3006b#0", progress=lambda outcome:
            write_json(args.destination / "item.checkpoint.json", outcome, control))
        item["fields"] = result["fields"]
        record = make_record(store.current, [item] if result["recordable"] else [], [])
        result["record_id"] = record[0]["id"] if record else None
        for request in result["clarifications"]:
            request["record_id"] = result["record_id"]
        p2 = {"format_version": 1, "run_id": data["run_id"], "meeting_id": store.current, "enabled": True, "items": [result]}
        clarifications = {"format_version": 1, "run_id": data["run_id"], "meeting_id": store.current, "requests": result["clarifications"]}
        errors = check(record, store) + validate_outputs(p2, clarifications, record, store)
        data.update(outcome={k: result[k] for k in ("state", "recordable", "termination_reason", "lookups")}, validation_errors=errors)
        if errors:
            raise ValueError("Case output failed validation")
        for name, value in (("TS3006b", record), ("TS3006b.p2", p2), ("TS3006b.clarifications", clarifications)):
            write_json(args.destination / (name + ".json"), value, control)
        write_json(args.destination / "review.json", {"human_review": {"status": "pending"},
            "relation_evidence": result["relation_evidence"], "source_item": source_item}, control)
        data["status"] = "COMPLETED"
        print(json.dumps(data["outcome"]), flush=True)
    except RunStopped as error:
        data.update(status=error.failure["status"], failure=error.failure)
    except Exception as error:
        data.update(status="FAILED", error_type=type(error).__name__)
    finally:
        control.seal(); trace.close()
        data.update(runtime_control=control.snapshot(), usage=scoped.usage_snapshot(), finished_at=utc_now(),
                    prior_source_unchanged=sha256(old) == data["inputs"]["prior_sha256"])
        stages.finish(stage="cross_meeting_agent", event="finished", status=data["status"])
        data["artifact_sha256"] = {p.relative_to(args.destination).as_posix(): sha256(p)
            for p in sorted(args.destination.rglob("*")) if p.is_file() and p.name != "run.json"}
        write_json(args.destination / "run.json", data)
    return 0 if data["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
