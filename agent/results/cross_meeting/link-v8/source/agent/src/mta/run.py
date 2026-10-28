"""CLI.  python -m mta.run <meeting.txt | SERIES> [--mode s2] [--out runs]

A series ID (e.g. TS3006) runs meetings a-d in order so later meetings can read earlier output.
Held-out series (ES2004, TS3007) need --allow-sc and should be run once, after the design is frozen.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .llm import LLM
from .pipeline import MODES, run_meeting


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="path to a transcript .txt, or a series ID such as TS3006")
    ap.add_argument("--mode", choices=MODES, default="s2")
    ap.add_argument("--out", type=Path, default=config.RUNS)
    ap.add_argument("--allow-sc", action="store_true", help="permit held-out series (ES2004, TS3007)")
    args = ap.parse_args(argv)

    if Path(args.target).suffix == ".txt":
        paths = [Path(args.target)]
    else:
        paths = [config.TRANSCRIPTS / f"{args.target}{c}.txt" for c in "abcd"]
    if any(p.stem[:-1] in config.SC_SERIES for p in paths) and not args.allow_sc:
        print("Held-out series: pass --allow-sc only for the final, frozen run.")
        return 2
    llm = LLM()
    code = 0
    for p in paths:
        res = run_meeting(p, args.mode, llm, args.out)
        print(json.dumps(res, ensure_ascii=False))
        if res["status"] != "COMPLETED":
            code = 1
            break  # later meetings need this one's output
    return code


if __name__ == "__main__":
    sys.exit(main())
