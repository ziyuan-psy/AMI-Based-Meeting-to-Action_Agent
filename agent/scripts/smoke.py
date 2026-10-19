"""Smoke run: TS3006 a then b in the given mode (default s2)."""
import json
import sys
from mta import config
from mta.llm import LLM
from mta.pipeline import run_meeting

mode = sys.argv[1] if len(sys.argv) > 1 else "s2"
llm = LLM()
for m in ("TS3006a", "TS3006b"):
    r = run_meeting(config.TRANSCRIPTS / f"{m}.txt", mode, llm, config.RUNS / "smoke")
    print(json.dumps(r, ensure_ascii=False)[:800], flush=True)
