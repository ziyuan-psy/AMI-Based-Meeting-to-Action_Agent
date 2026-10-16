"""Create readable TXT inputs from the 24 selected AMI dialogueActs files."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERIES_BY_SPLIT = {
    "SB": ("ES2011", "IS1008", "TS3004", "TS3006"),
    "SC": ("ES2004", "TS3007"),
}
TRANSCRIPT_FIELDS = ("id", "speaker", "starttime", "endtime", "text")


def escape_text(text: str) -> str:
    """Keep each segment on one line without losing literal control characters."""
    return (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def load_records(source: Path, meeting: str) -> list[dict]:
    records = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(records, list) or not records:
        raise ValueError(f"{meeting}: expected a nonempty JSON array")
    seen_ids = set()
    for index, record in enumerate(records, start=1):
        location = f"{meeting}: record {index}"
        if not isinstance(record, dict):
            raise ValueError(f"{location}: expected a JSON object")
        for field in TRANSCRIPT_FIELDS:
            if not isinstance(record.get(field), str):
                raise ValueError(f"{location}: {field} must be a string")
        if not record["id"].startswith(meeting + "."):
            raise ValueError(f"{location}: ID does not belong to this meeting")
        if record["id"] in seen_ids:
            raise ValueError(f"{location}: duplicate ID")
        seen_ids.add(record["id"])
        if any(char.isspace() or char in "()" for char in record["id"]):
            raise ValueError(f"{location}: ID is not a valid transcript token")
        if not record["speaker"] or any(
            char.isspace() or char in "()" for char in record["speaker"]
        ):
            raise ValueError(f"{location}: speaker is not a valid transcript token")
        try:
            start = Decimal(record["starttime"])
            end = Decimal(record["endtime"])
        except InvalidOperation as error:
            raise ValueError(f"{location}: invalid timestamp") from error
        if not start.is_finite() or not end.is_finite():
            raise ValueError(f"{location}: nonfinite timestamp")
        # Preserve source timing anomalies; exporting must not repair annotations.
        if any(
            char.isspace() or char in "[]"
            for field in ("starttime", "endtime")
            for char in record[field]
        ):
            raise ValueError(f"{location}: timestamp is not a valid transcript token")
    return records


def render_transcript(meeting: str, split: str, records: list[dict]) -> str:
    lines = [
        "# AMI reconstructed transcript v1",
        f"# Meeting: {meeting}",
        f"# Split: {split}",
        f"# Source: ami-corpus/dialogueActs/{meeting}.json",
        "# Format: [starttime --> endtime] speaker (dialogue_act_id): text",
        r"# Times are seconds; text escapes: \\, \n, \r, \t.",
        "",
    ]
    for record in records:
        lines.append(
            f"[{record['starttime']} --> {record['endtime']}] "
            f"{record['speaker']} ({record['id']}): {escape_text(record['text'])}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=REPO_ROOT.parent / "ami-corpus" / "dialogueActs",
        help="Original dialogueActs directory (read only)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "transcripts",
        help="Destination containing SB/ and SC/ subdirectories",
    )
    parser.add_argument(
        "--split",
        choices=("SB", "SC", "all"),
        default="SB",
        help="SB by default; SC conversion must be requested explicitly",
    )
    args = parser.parse_args()
    splits = tuple(SERIES_BY_SPLIT) if args.split == "all" else (args.split,)

    # Validate every requested input before writing any outputs in this run.
    prepared = []
    for split in splits:
        for series in SERIES_BY_SPLIT[split]:
            for suffix in "abcd":
                meeting = series + suffix
                source = args.source_dir / f"{meeting}.json"
                records = load_records(source, meeting)
                destination = args.output_dir / split / f"{meeting}.txt"
                prepared.append(
                    (meeting, split, destination, render_transcript(meeting, split, records), len(records))
                )

    for meeting, split, destination, contents, count in prepared:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
        print(f"{split}/{meeting}.txt: {count} segments")
    print(f"Wrote {len(prepared)} TXT files; source JSON files were read only.")


if __name__ == "__main__":
    main()
