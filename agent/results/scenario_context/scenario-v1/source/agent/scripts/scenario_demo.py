"""Live document-context verification of genuine saved development input; no gold or final evaluation.

PYTHONPATH=src python scripts/scenario_demo.py <new-output-folder> [--case lcd|evaluation]
The lcd case isolates the first clause of a compound saved MVP decision. The complete source is preserved.
No expected query, PDF page, passage ID, citation or document assessment is supplied to the Agent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from uuid import uuid4

from mta import config
from mta.agent import MeetingBudget, run_agent
from mta.baseline import check_destination
from mta.control import RunControl, RunStopped
from mta.llm import LLM
from mta.observation import REPOSITORY, runtime_identity, sha256, source_files, utc_now, write_json, write_text
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
    parser.add_argument("--case", choices=("lcd", "evaluation"), default="lcd")
    args = parser.parse_args(argv)
    check_destination(args.destination)
    if not config.P2_ENABLED:
        raise ValueError("This case requires P2")
    source = config.ROOT / "results/mvp/s2/TS3006d.json"
    identifier = "TS3006d.sys.s." + ("1" if args.case == "lcd" else "6")
    original = next(r for r in json.loads(source.read_text(encoding="utf-8")) if r["id"] == identifier)
    text = original["text"].split(";")[0].rstrip(".") + "." if args.case == "lcd" else original["text"]
    item = {"type": original["type"], "text": text, "status": original["status"],
            "evidence_da_ids": [r["locator"].split(" ")[0] for r in original["reference"] if r["source_type"] == "dialogue"],
            "owner_ids": [], "owner_basis": "missing", "deadline": None, "deadline_basis": "missing",
            "verification_request": "Check this item's project context against AMI scenario instructions. Search for relevant passages and read their surrounding page context before selecting documentary support. Explicitly assess passages you use, preserve actual meeting evidence, and leave unsupported or conflicting claims pending. You choose the query, page, further reading/search and whether to stop."}
    llm = LLM()
    args.destination.mkdir(parents=True)
    (args.destination / ".gitattributes").write_text("* -text -whitespace\n", encoding="utf-8")
    data = {"format_version": 1, "status": "RUNNING", "execution_kind": "live_scenario_context_agent_stage",
            "run_id": uuid4().hex, "created_at": utc_now(), "meeting_id": "TS3006d", "case": args.case,
            "scope": "Focused S1 document-context check of genuine saved input; lcd uses its first clause. No extraction/summary rerun, gold access, judge or final evaluation. Explicit context-verification request; not a claim of spontaneous document use.",
            "input": {"source": source.relative_to(REPOSITORY).as_posix(), "source_item_id": identifier,
                      "source_sha256": sha256(source), "transcript_sha256": sha256(config.TRANSCRIPTS / "TS3006d.txt"),
                      "scenario_sha256": sha256(config.SCENARIO)}, **runtime_identity(llm)}
    for path in source_files() + [config.SCHEMA, config.P2_SCHEMA, config.SCENARIO, config.PARTICIPANTS]:
        target = args.destination / "source" / path.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    write_json(args.destination / "input.json", {"original_record": original, "selected_item": item})
    write_json(args.destination / "run.json", data)
    stages, trace = StageTrace(args.destination / "stages.jsonl"), Trace(args.destination / "agent.trace.jsonl")
    control, budget = RunControl(trace=stages), MeetingBudget()
    scoped = llm.scoped(control)
    store = Store("TS3006d", allow_prior=False)
    flag_items([item], store)
    item["run_id"] = data["run_id"]
    try:
        control.enter("document_context_agent")
        result = run_agent(scoped, store, item, budget, trace, "TS3006d#0", progress=lambda outcome:
            write_json(args.destination / "item.checkpoint.json", outcome, control))
        item["fields"] = result["fields"]
        record = make_record(store.current, [item] if result["recordable"] else [], [])
        result["record_id"] = record[0]["id"] if record else None
        for request in result["clarifications"]:
            request["record_id"] = result["record_id"]
        p2 = {"format_version": 1, "run_id": data["run_id"], "meeting_id": store.current, "enabled": True, "items": [result]}
        requests = {"format_version": 1, "run_id": data["run_id"], "meeting_id": store.current, "requests": result["clarifications"]}
        errors = check(record, store) + validate_outputs(p2, requests, record, store)
        data.update(outcome={k: result[k] for k in ("state", "recordable", "termination_reason", "lookups")},
                    retained_documents=sum(w["retained"] for w in result["document_evidence"]), validation_errors=errors)
        if errors:
            raise ValueError("Output validation failed")
        for name, value in (("TS3006d", record), ("TS3006d.p2", p2), ("TS3006d.clarifications", requests)):
            write_json(args.destination / (name + ".json"), value, control)
        write_text(args.destination / "TS3006d_report.md", render_report(record, store, "s1", {}, p2), control)
        data["status"] = "COMPLETED"
        print(json.dumps({"outcome": data["outcome"], "retained_documents": data["retained_documents"]}), flush=True)
    except RunStopped as error:
        data.update(status=error.failure["status"], failure=error.failure)
    except Exception as error:
        data.update(status="FAILED", error_type=type(error).__name__)
    finally:
        control.seal(); trace.close()
        data.update(runtime_control=control.snapshot(), usage=scoped.usage_snapshot(), finished_at=utc_now())
        stages.finish(stage="document_context_agent", event="finished", status=data["status"])
        data["artifact_sha256"] = {p.relative_to(args.destination).as_posix(): sha256(p)
            for p in sorted(args.destination.rglob("*")) if p.is_file() and p.name != "run.json"}
        write_json(args.destination / "run.json", data)
    return 0 if data["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
