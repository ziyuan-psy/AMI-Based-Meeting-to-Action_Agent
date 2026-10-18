"""Parser behavior tests using synthetic SB-shaped inputs; no SC fixtures."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_transcripts import render_transcript
from transcript_parser import (
    REPO_ROOT,
    TranscriptParseError,
    TranscriptTimingWarning,
    main,
    parse_transcript,
    parse_transcript_file,
)


def segment(meeting="ES2011a", number=1, text="Hello .", start="1.20", end="2.30"):
    return {
        "id": f"{meeting}.A.dialog-act.test.{number}",
        "speaker": "A",
        "starttime": start,
        "endtime": end,
        "text": text,
    }


def transcript(*records):
    return render_transcript("ES2011a", "SB", list(records or [segment()]))


class TranscriptParserTests(unittest.TestCase):
    def test_lossless_transcript_fields_and_explicit_missing_metadata(self):
        original = segment(text="  中文 <gap> \\n \\ tab\t newline\n CR\r (x):  ")
        record = parse_transcript(transcript(original))[0]
        for key, value in original.items():
            self.assertEqual(record[key], value)
        self.assertEqual(
            set(record),
            {"id", "speaker", "starttime", "startwordid", "endtime", "endwordid", "text", "label", "attributes"},
        )
        self.assertIsNone(record["startwordid"])
        self.assertIsNone(record["endwordid"])
        self.assertIsNone(record["label"])
        self.assertEqual(record["attributes"], {})

    def test_empty_utterance_and_original_order_are_preserved(self):
        original = [segment(number=2, text="", start="3.0", end="4.0"), segment(number=1)]
        result = parse_transcript(transcript(*original))
        self.assertEqual([r["id"] for r in result], [r["id"] for r in original])
        self.assertEqual(result[0]["text"], "")

    def test_bom_crlf_and_provenance_comment_without_source_access(self):
        value = transcript().replace(
            "# Source: ami-corpus/dialogueActs/ES2011a.json",
            "# Source: missing/path/must/not/be/opened.json",
        )
        result = parse_transcript("\ufeff" + value.replace("\n", "\r\n"))
        self.assertEqual(result[0]["text"], "Hello .")

    def test_header_is_optional_when_meeting_can_be_read_from_ids(self):
        body = "[1.20 --> 2.30] A (ES2011a.A.dialog-act.test.1): Hello .\n"
        self.assertEqual(parse_transcript(body)[0]["id"], segment()["id"])

    def test_invalid_data_line_is_not_silently_skipped(self):
        with self.assertRaisesRegex(TranscriptParseError, "line 2"):
            parse_transcript("# comment\nThis line has no structured prefix\n")

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(TranscriptParseError, "duplicate dialogue-act ID"):
            parse_transcript(transcript(segment(), segment()))

    def test_unknown_and_incomplete_escapes_are_rejected(self):
        prefix = "[1 --> 2] A (ES2011a.A.dialog-act.test.1): "
        for text in ["bad\\q", "unfinished\\"]:
            with self.subTest(text=text), self.assertRaisesRegex(TranscriptParseError, "escape"):
                parse_transcript(prefix + text)

    def test_mixed_meetings_and_expected_meeting_conflicts_are_rejected(self):
        with self.assertRaisesRegex(TranscriptParseError, "different meeting"):
            parse_transcript(transcript(segment(), segment(meeting="ES2011b")))
        with self.assertRaisesRegex(TranscriptParseError, "conflicts with expected meeting"):
            parse_transcript(transcript(), expected_meeting="ES2011b")

    def test_speaker_must_match_id(self):
        with self.assertRaisesRegex(TranscriptParseError, "speaker conflicts"):
            parse_transcript(transcript().replace(
                "] A (ES2011a.A.", "] B (ES2011a.A."
            ))

    def test_numeric_finite_timestamps_are_required(self):
        for value in ["NaN", "Infinity", "not-a-time"]:
            with self.subTest(value=value), self.assertRaises(TranscriptParseError):
                parse_transcript(transcript(segment(start=value)))

    def test_reversed_source_time_is_flagged_without_repair(self):
        with self.assertWarns(TranscriptTimingWarning):
            record = parse_transcript(transcript(segment(start="78.122", end="78.001")))[0]
        self.assertEqual(record["starttime"], "78.122")
        self.assertEqual(record["endtime"], "78.001")

    def test_empty_input_bad_versions_and_duplicate_headers_fail(self):
        for value in ["", "# comments only\n", transcript().replace("transcript v1", "transcript v2"), "# Meeting: ES2011a\n" + transcript()]:
            with self.subTest(value=value[:30]), self.assertRaises(TranscriptParseError):
                parse_transcript(value)

    def test_file_name_checks_meeting_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ES2011b.txt"
            path.write_text(transcript(), encoding="utf-8")
            with self.assertRaisesRegex(TranscriptParseError, "expected meeting"):
                parse_transcript_file(path)

    def test_cli_writes_flat_json_and_requires_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ES2011a.txt"
            output = root / "output.json"
            source.write_text(transcript(), encoding="utf-8")
            args = [str(source), "--output", str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))[0]["id"], segment()["id"])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(args)
            self.assertEqual(error.exception.code, 2)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args + ["--overwrite"]), 0)

    def test_malformed_batch_does_not_write_partial_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            source.mkdir()
            (source / "ES2011a.txt").write_text(transcript(), encoding="utf-8")
            (source / "ES2011b.txt").write_text("malformed\n", encoding="utf-8")
            output = root / "output"
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main([str(source), "--output", str(output)])
            self.assertFalse(output.exists())

    def test_annotation_destinations_are_protected_even_with_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ES2011a.txt"
            source.write_text(transcript(), encoding="utf-8")
            for destination in [
                REPO_ROOT / "revised_gold_output" / "ES2011a.json",
                REPO_ROOT.parent / "ami-corpus" / "dialogueActs" / "ES2011a.json",
            ]:
                with self.subTest(destination=destination.name), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main([str(source), "--output", str(destination), "--overwrite"])


if __name__ == "__main__":
    unittest.main()
