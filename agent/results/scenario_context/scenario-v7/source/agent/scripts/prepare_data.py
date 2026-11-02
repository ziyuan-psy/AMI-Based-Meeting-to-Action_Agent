"""Build data/participants.json: meeting -> speaker letter -> participant id and role.

Reads only attributes.participant / attributes.role from the AMI dialogueActs
files (never labels or other annotations). Fails if a letter maps to more than
one (participant, role) pair within a meeting.

Usage: python scripts/prepare_data.py --ami-dir ".../ami-corpus/ami-corpus"
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mta import config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ami-dir", required=True, help="folder containing dialogueActs/")
    args = ap.parse_args()
    da_dir = Path(args.ami_dir) / "dialogueActs"
    table, problems = {}, []
    for txt in sorted(config.TRANSCRIPTS.glob("*.txt")):
        meeting = txt.stem
        acts = json.loads((da_dir / f"{meeting}.json").read_text(encoding="utf-8"))
        seen: dict[str, dict] = {}
        for a in acts:
            attr = a.get("attributes") or {}
            pair = {"participant": attr.get("participant"), "role": attr.get("role")}
            letter = a["speaker"]
            if letter not in seen:
                seen[letter] = {**pair, "first_da_id": a["id"]}
            elif {k: seen[letter][k] for k in pair} != pair:
                problems.append(f"{meeting} {letter}: {seen[letter]} vs {pair}")
        for letter, pair in seen.items():
            if not pair["participant"] or not pair["role"]:
                problems.append(f"{meeting} {letter}: missing participant/role")
        table[meeting] = seen
    if problems:
        print("\n".join(problems))
        return 1
    config.PARTICIPANTS.write_text(json.dumps(table, indent=1), encoding="utf-8")
    print(f"wrote {config.PARTICIPANTS} for {len(table)} meetings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
