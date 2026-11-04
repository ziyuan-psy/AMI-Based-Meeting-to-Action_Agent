"""Shared parser imports and CLI compatibility; synthetic input, no API calls."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = AGENT_ROOT.parent
sys.path.insert(0, str(AGENT_ROOT / "src"))

from mta import transcript_parser as adapter  # noqa: E402
import transcript_parser as shared  # noqa: E402


def test_agent_uses_the_shared_parser_and_exception_classes():
    from mta import pipeline, store

    for name in adapter.__all__:
        assert getattr(adapter, name) is getattr(shared, name)
    assert adapter.REPO_ROOT == REPOSITORY_ROOT
    assert pipeline.parse_transcript_file is shared.parse_transcript_file
    assert pipeline.TranscriptParseError is shared.TranscriptParseError
    assert store.parse_transcript_file is shared.parse_transcript_file


def run_python(cwd, pythonpath, *args):
    env = {**os.environ, "PYTHONPATH": pythonpath}
    return subprocess.run(
        [sys.executable, "-X", "utf8", *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


@pytest.mark.parametrize(
    ("cwd", "pythonpath"),
    [(REPOSITORY_ROOT, str(Path("agent") / "src")), (AGENT_ROOT, "src")],
    ids=["repository-root", "agent-directory"],
)
def test_shared_imports_work_with_existing_launch_paths(cwd, pythonpath):
    result = run_python(cwd, pythonpath, "-c", "\n".join([
        "from mta import transcript_parser as adapter",
        "import transcript_parser as shared",
        "assert adapter.parse_transcript_file is shared.parse_transcript_file",
        "assert adapter.TranscriptParseError is shared.TranscriptParseError",
        "assert adapter.REPO_ROOT == shared.REPO_ROOT",
        "try:",
        "    adapter.parse_transcript('malformed transcript')",
        "except shared.TranscriptParseError:",
        "    pass",
        "else:",
        "    raise AssertionError('expected the shared parse exception')",
    ]))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("cwd", "pythonpath", "entrypoint"),
    [
        (REPOSITORY_ROOT, str(Path("agent") / "src"), ["transcript_parser.py"]),
        (AGENT_ROOT, "src", ["-m", "mta.transcript_parser"]),
        (REPOSITORY_ROOT, ".", [str(Path("agent") / "src" / "mta" / "transcript_parser.py")]),
    ],
    ids=["root-cli", "agent-module-cli", "agent-script-cli"],
)
def test_parser_cli_preserves_records_and_reports_invalid_input(tmp_path, cwd, pythonpath, entrypoint):
    source = tmp_path / "TS3006a.txt"
    output = tmp_path / "parsed.json"
    source.write_text(
        "# AMI reconstructed transcript v1\n# Meeting: TS3006a\n"
        "[1.20 --> 2.30] A (TS3006a.A.dialog-act.test.1): Hello .\n",
        encoding="utf-8",
    )
    result = run_python(cwd, pythonpath, *entrypoint, source, "--output", output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == shared.parse_transcript_file(source)

    source.write_text("malformed transcript\n", encoding="utf-8")
    rejected = tmp_path / "rejected.json"
    result = run_python(cwd, pythonpath, *entrypoint, source, "--output", rejected)
    assert result.returncode == 2
    assert "expected '[start --> end] speaker (id): text'" in result.stderr
    assert not rejected.exists()
