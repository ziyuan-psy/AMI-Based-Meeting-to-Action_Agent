"""Paths, model name and the code-enforced limits for the follow-up agent."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
TRANSCRIPTS = DATA / "transcripts"
PARTICIPANTS = DATA / "participants.json"
SCENARIO = DATA / "scenario_text.txt"
SCHEMA = ROOT / "schemas" / "output_schema.json"
RUNS = ROOT / "runs"

def _load_dotenv() -> None:
    """Read KEY=VALUE lines from <project>/.env (git-ignored) without overriding real env vars."""
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

MODEL = os.environ.get("MTA_MODEL", "claude-sonnet-5-5")  # Anthropic path

# OpenAI-compatible path (Portkey gateway); used when OPENAI_API_KEY is set.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "@30875-fall26-openai/gpt-5-nano")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.portkey.ai/v1")
OPENAI_MIN_COMPLETION_TOKENS = 32000  # reasoning models spend part of the budget on hidden reasoning

# Held-out series: never used for prompt/threshold tuning (see README).
SC_SERIES = {"ES2004", "TS3007"}

# Agent limits (enforced in code, not by the model).
MAX_CALLS_PER_ITEM = 4
MAX_CALLS_PER_MEETING = 30
MAX_INVALID_ACTIONS = 2
MAX_NO_PROGRESS = 2

# Extraction chunking.
CHUNK = 150
OVERLAP = 20

# POSSIBLE_PRIOR flag: content-word Jaccard against earlier system items (calibrated on SB gold).
PRIOR_JACCARD = 0.08

