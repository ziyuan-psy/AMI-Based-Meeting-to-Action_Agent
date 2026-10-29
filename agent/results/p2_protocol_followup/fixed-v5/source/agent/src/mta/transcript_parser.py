"""Compatibility entry point for the repository's shared TXT parser.

Keep parsing logic in the root ``transcript_parser.py``. Locating that module
here preserves agent commands run from ``agent/`` with only ``PYTHONPATH=src``.
"""
from __future__ import annotations

import sys
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
# Put the shared module before this file's directory, including direct execution
# of this compatibility script. All agent callers share the same module/classes.
if not sys.path or sys.path[0] != str(_REPOSITORY_ROOT):
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from transcript_parser import (  # noqa: E402
    FORMAT_HEADER,
    MEETING_PATTERN,
    REPO_ROOT,
    SEGMENT_PATTERN,
    TranscriptParseError,
    TranscriptTimingWarning,
    main,
    parse_transcript,
    parse_transcript_file,
)

__all__ = [
    "FORMAT_HEADER",
    "MEETING_PATTERN",
    "REPO_ROOT",
    "SEGMENT_PATTERN",
    "TranscriptParseError",
    "TranscriptTimingWarning",
    "main",
    "parse_transcript",
    "parse_transcript_file",
]


if __name__ == "__main__":
    raise SystemExit(main())
