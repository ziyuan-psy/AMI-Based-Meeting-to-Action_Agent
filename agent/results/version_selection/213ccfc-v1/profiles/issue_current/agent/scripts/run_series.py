"""Run a->d, skipping only verified completed record/report pairs. Usage: run_series.py TS3006 s2"""
import json
import sys
from mta import config
from mta.llm import LLM
from mta.pipeline import run_meeting, saved_run_complete

def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    series, mode = args[0], args[1]
    root = config.RUNS / (args[2] if len(args) > 2 else "dev")
    if series in config.SC_SERIES:
        raise SystemExit("held-out series: use mta.run --allow-sc for the frozen final run")
    llm = LLM()
    for c in "abcd":
        m = f"{series}{c}"
        if saved_run_complete(root / mode / f"{m}.json"):
            print(m, "completed and verified, skipped", flush=True)
            continue
        result = run_meeting(config.TRANSCRIPTS / f"{m}.txt", mode, llm, root)
        print(json.dumps(result, ensure_ascii=False)[:500], flush=True)
        if result["status"] != "COMPLETED":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
