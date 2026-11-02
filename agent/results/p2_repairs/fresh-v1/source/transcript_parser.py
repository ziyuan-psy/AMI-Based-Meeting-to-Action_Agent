"""Parse reconstructed AMI TXT inputs into dialogueActs-style JSON records."""

from __future__ import annotations

import argparse
import json
import re
import warnings
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Union


REPO_ROOT = Path(__file__).resolve().parent
MEETING_PATTERN = re.compile(r"[A-Za-z]+[0-9]+[a-d]")
SEGMENT_PATTERN = re.compile(
    r"\[(?P<starttime>[^\s\[\]]+) --> (?P<endtime>[^\s\[\]]+)\] "
    r"(?P<speaker>[^\s()]+) \((?P<id>[^\s()]+)\): (?P<text>.*)"
)
FORMAT_HEADER = "# AMI reconstructed transcript v1"


class TranscriptParseError(ValueError):
    """The input cannot be parsed without guessing or dropping data."""


class TranscriptTimingWarning(UserWarning):
    """A source timing anomaly was preserved in the parsed output."""


def _error(line: int, message: str) -> TranscriptParseError:
    return TranscriptParseError(f"line {line}: {message}")


def _unescape_text(text: str, line: int) -> str:
    escapes = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
    result = []
    position = 0
    while position < len(text):
        character = text[position]
        if character != "\\":
            if character in "\r\t":
                raise _error(line, "text contains an unescaped control character")
            result.append(character)
            position += 1
            continue
        if position + 1 == len(text) or text[position + 1] not in escapes:
            raise _error(line, "invalid or incomplete text escape")
        result.append(escapes[text[position + 1]])
        position += 2
    return "".join(result)


def parse_transcript(text: str, *, expected_meeting: Optional[str] = None) -> list[dict]:
    """Return a flat nine-field array, preserving the five available TXT fields.

    This function reads no files or external metadata. Missing word IDs and the
    human dialogue-act label are null; attributes are empty pending lookup.
    Malformed inputs raise TranscriptParseError. Reversed timing is retained
    and reported through TranscriptTimingWarning.
    """
    if expected_meeting is not None and not MEETING_PATTERN.fullmatch(expected_meeting):
        raise TranscriptParseError("expected_meeting is not a valid AMI meeting ID")
    meeting = expected_meeting
    header_seen = False
    seen_ids = set()
    records = []
    # Normalize physical Windows line endings, not escaped transcript text.
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").split("\n")
    for line_number, line in enumerate(lines, start=1):
        if not line or line.isspace():
            continue
        if line.startswith("#"):
            if line.startswith("# AMI reconstructed transcript ") and line != FORMAT_HEADER:
                raise _error(line_number, "unsupported transcript format version")
            if line.startswith("# Meeting:"):
                declared = line.partition(":")[2].strip()
                if header_seen or records:
                    raise _error(line_number, "duplicate or late meeting header")
                if not MEETING_PATTERN.fullmatch(declared):
                    raise _error(line_number, "invalid meeting header")
                if meeting is not None and declared != meeting:
                    raise _error(line_number, "meeting header conflicts with expected meeting")
                meeting = declared
                header_seen = True
            # Source, split and other comment headers are never opened as paths.
            continue
        match = SEGMENT_PATTERN.fullmatch(line)
        if match is None:
            raise _error(line_number, "expected '[start --> end] speaker (id): text'")
        fields = match.groupdict()
        id_parts = fields["id"].split(".", 2)
        if len(id_parts) != 3 or not MEETING_PATTERN.fullmatch(id_parts[0]):
            raise _error(line_number, "invalid dialogue-act ID")
        if id_parts[1] != fields["speaker"]:
            raise _error(line_number, "speaker conflicts with dialogue-act ID")
        if meeting is None:
            meeting = id_parts[0]
        if id_parts[0] != meeting:
            raise _error(line_number, "segment belongs to a different meeting")
        if fields["id"] in seen_ids:
            raise _error(line_number, "duplicate dialogue-act ID")
        seen_ids.add(fields["id"])
        try:
            start = Decimal(fields["starttime"])
            end = Decimal(fields["endtime"])
        except InvalidOperation as error:
            raise _error(line_number, "timestamp must be numeric seconds") from error
        if not start.is_finite() or not end.is_finite():
            raise _error(line_number, "timestamp must be finite")
        if end < start:
            warnings.warn(
                f"{meeting}: line {line_number}: endtime precedes starttime; preserved unchanged",
                TranscriptTimingWarning,
                stacklevel=2,
            )
        records.append(
            {
                "id": fields["id"],
                "speaker": fields["speaker"],
                "starttime": fields["starttime"],
                "startwordid": None,
                "endtime": fields["endtime"],
                "endwordid": None,
                "text": _unescape_text(fields["text"], line_number),
                "label": None,
                "attributes": {},
            }
        )
    if not records:
        raise TranscriptParseError("transcript contains no dialogue-act segments")
    return records


def parse_transcript_file(
    path: Union[str, Path], *, expected_meeting: Optional[str] = None
) -> list[dict]:
    """Read one UTF-8 TXT; an AMI filename also acts as a meeting-ID check."""
    path = Path(path)
    if expected_meeting is None and MEETING_PATTERN.fullmatch(path.stem):
        expected_meeting = path.stem
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return parse_transcript(handle.read(), expected_meeting=expected_meeting)


def _check_destination(destination: Path, *, overwrite: bool) -> None:
    if destination.suffix.lower() != ".json":
        raise ValueError("output files must have a .json extension")
    protected_roots = (
        REPO_ROOT / "revised_gold_output",
        REPO_ROOT.parent / "ami-corpus",
        REPO_ROOT / ".git",
    )
    if any(destination.resolve().is_relative_to(root.resolve()) for root in protected_roots):
        raise ValueError("output must not be inside source annotations, revised gold or .git")
    if destination.exists() and (not overwrite or not destination.is_file()):
        raise FileExistsError(f"output already exists: {destination.name}; use --overwrite for derived files")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="One TXT file or one split directory (SB or SC)")
    parser.add_argument("--output", "-o", type=Path, required=True, help="JSON file, or directory for batch input")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing derived JSON outputs")
    args = parser.parse_args(argv)
    try:
        if args.input.is_file():
            if args.input.suffix.lower() != ".txt":
                raise ValueError("input file must have a .txt extension")
            pairs = [(args.input, args.output)]
        elif args.input.is_dir():
            if args.output.exists() and not args.output.is_dir():
                raise ValueError("batch output must be a directory")
            sources = sorted(args.input.glob("*.txt"))
            if not sources:
                raise ValueError("input directory contains no TXT files; select SB or SC explicitly")
            pairs = [(source, args.output / (source.stem + ".json")) for source in sources]
        else:
            raise FileNotFoundError("input file or directory does not exist")
        # Preflight the entire batch before creating or replacing output files.
        prepared = []
        for source, destination in pairs:
            _check_destination(destination, overwrite=args.overwrite)
            try:
                records = parse_transcript_file(source)
            except (TranscriptParseError, UnicodeError) as error:
                raise TranscriptParseError(f"{source.name}: {error}") from error
            prepared.append((source, destination, records))
        for source, destination, records in prepared:
            destination.parent.mkdir(parents=True, exist_ok=True)
            mode = "w" if args.overwrite else "x"
            with destination.open(mode, encoding="utf-8", newline="\n") as handle:
                json.dump(records, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            print(f"{source.name} -> {destination.name}: {len(records)} segments")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
