"""Release identity and artifact checks, independent of model clients and gold labels."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path("agent/results/candidate_acceptance/66a21d7-v1")
PROFILE = "recall_candidate_v1"
REFERENCE = "66a21d7c58651ef79c8d9e8f146ebd3880f35385"
# Offline scorers are captured for provenance but are not called by the live UI.
# Keep executing their frozen bytes in the private runtime while allowing the
# checkout's evaluation tools to evolve independently.
EVALUATION_ONLY_FILES = frozenset({"agent/scripts/full_eval.py", "agent/src/mta/evaluate.py"})


class ReleaseMismatch(ValueError):
    """Only explicit, credential-free diagnostics may be shown in the UI."""


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_lines(path: Path):
    """A polling reader may encounter an unfinished final JSONL line."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def verify_artifacts(directory: Path, meeting_id: str):
    meta = load(directory / f"{meeting_id}.run.json")
    expected = load(ROOT / PACKAGE / "study.json")["runtime"]["quality_policy"]
    if (meta.get("status") != "COMPLETED" or meta.get("quality_policy") != expected
            or meta.get("meeting_id") != meeting_id or meta.get("mode") != "s2"):
        raise ValueError("A completed accepted-profile run is required.")
    result = meta["result"]
    for suffix, key in ((".json", "output_sha256"), ("_report.md", "report_sha256"),
                        (".p2.json", "p2_sha256"), (".clarifications.json", "clarifications_sha256")):
        if digest(directory / (meeting_id + suffix)) != result.get(key):
            raise ValueError("A completed run artifact does not match its recorded hash.")
    for suffix in (".p2.json", ".clarifications.json"):
        sidecar = load(directory / (meeting_id + suffix))
        if sidecar.get("run_id") != meta["run_id"] or sidecar.get("meeting_id") != meeting_id:
            raise ValueError("Run and sidecar identities do not match.")
    if load(directory / (meeting_id + ".p2.json")).get("enabled") != expected["p2_enabled"]:
        raise ValueError("The sidecar's effective P2 policy does not match the release.")
    return meta


def materialize_runtime(root: Path = ROOT) -> Path:
    """Use the frozen bytes, without rewriting runtime files in the checkout.

    Git's Windows line-ending conversion changes some captured byte hashes. The
    committed source snapshot preserves the accepted bytes. Require checkout
    generation content to agree (allowing ONLY newline conversion), then execute
    that exact snapshot in an ignored, private runtime directory. Offline scoring
    files may evolve in the checkout; their frozen copies are still verified.
    """
    package = root / PACKAGE
    study = load(package / "study.json")
    if study.get("commit") != REFERENCE:
        raise ReleaseMismatch("The frozen study does not identify the nominated runtime reference.")
    if load(package / "acceptance.json").get("status") != "PASSED_WITH_DOCUMENTED_LIMITATIONS":
        raise ReleaseMismatch("The agent release does not have a completed acceptance verdict.")
    identity = hashlib.sha256(json.dumps(study["source_sha256"], sort_keys=True).encode()).hexdigest()[:16]
    destination = root / "agent/runs/ui-runtime" / identity
    for relative, expected in study["source_sha256"].items():
        source = package / "source" / relative
        frozen = source.read_bytes()
        if hashlib.sha256(frozen).hexdigest() != expected:
            raise ReleaseMismatch("Frozen runtime snapshot failed verification: " + relative)
        if relative not in EVALUATION_ONLY_FILES:
            current = (root / relative).read_bytes()
            if current.replace(b"\r\n", b"\n") != frozen.replace(b"\r\n", b"\n"):
                raise ReleaseMismatch("Agent source differs from the accepted release: " + relative)
        target = destination / relative
        if not target.exists() or digest(target) != expected:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(frozen)
    # Only runtime inputs: no annotations, evaluation files, or held-out meetings.
    data = destination / "agent/data"
    data.mkdir(parents=True, exist_ok=True)
    for name in ("participants.json", "scenario_text.txt"):
        source = package / "inputs" / name
        target = data / name
        if not target.exists() or digest(target) != digest(source):
            shutil.copyfile(source, target)
    transcripts = data / "transcripts"
    transcripts.mkdir(exist_ok=True)
    sources = [root / "transcripts/SB" / (series + letter + ".txt")
               for series in ("ES2011", "IS1008", "TS3004", "TS3006") for letter in "abcd"]
    for source in sources:
        target = transcripts / source.name
        if not target.exists() or digest(target) != digest(source):
            shutil.copyfile(source, target)
    return destination
