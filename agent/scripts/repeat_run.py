"""Repeat one system on a series to measure run-to-run variance. Each repetition has its own output and cache folder.

Usage: repeat_run.py TS3006 b0 <repetition number> [root]      (output: runs/<root>/r<N>/<mode>/)
"""
import json
import sys

from mta import config
from mta.llm import LLM
from mta.pipeline import run_meeting

series, mode, rep = sys.argv[1], sys.argv[2], sys.argv[3]
root = config.RUNS / (sys.argv[4] if len(sys.argv) > 4 else "reps") / f"r{rep}"
if series in config.SC_SERIES:
    sys.exit("held-out series: not allowed here")
llm = LLM()
for c in "abcd":
    m = f"{series}{c}"
    if (root / mode / f"{m}.json").exists():
        continue
    r = run_meeting(config.TRANSCRIPTS / f"{m}.txt", mode, llm, root, cache_dir=root / "_cache")
    print(json.dumps({k: r.get(k) for k in ("mode", "status", "meeting_id", "items", "reason")}), flush=True)
    if r["status"] != "COMPLETED":
        break
