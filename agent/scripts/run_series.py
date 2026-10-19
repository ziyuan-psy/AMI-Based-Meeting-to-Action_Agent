"""Run a whole series (a->d) in a mode, skipping meetings whose output already exists.  Usage: run_series.py TS3006 s2"""
import json
import sys
from mta import config
from mta.llm import LLM
from mta.pipeline import run_meeting

series, mode = sys.argv[1], sys.argv[2]
root = config.RUNS / (sys.argv[3] if len(sys.argv) > 3 else "dev")
if series in config.SC_SERIES:
    sys.exit("held-out series: use mta.run --allow-sc for the frozen final run")
llm = LLM()
for c in "abcd":
    m = f"{series}{c}"
    if (root / mode / f"{m}.json").exists():
        print(m, "exists, skipped", flush=True)
        continue
    r = run_meeting(config.TRANSCRIPTS / f"{m}.txt", mode, llm, root)
    print(json.dumps(r, ensure_ascii=False)[:500], flush=True)
    if r["status"] != "COMPLETED":
        break
