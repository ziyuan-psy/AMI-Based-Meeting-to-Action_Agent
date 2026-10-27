"""Paths, model name and the code-enforced limits for the follow-up agent."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
TRANSCRIPTS = DATA / "transcripts"
PARTICIPANTS = DATA / "participants.json"
SCENARIO = DATA / "scenario_text.txt"
SCHEMA = ROOT / "schemas" / "output_schema.json"
P2_SCHEMA = ROOT / "schemas" / "p2_output_schema.json"
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

# Evaluation judge (alignment, evidence support, summary quality). Default "pipeline": the same model as above.
# MTA_JUDGE=claude uses Claude via the Anthropic SDK instead (needs `anthropic` and ANTHROPIC_API_KEY).
JUDGE = os.environ.get("MTA_JUDGE", "pipeline")
# Alignment with gold is judged this many times; a pair counts when a majority of the runs give it.
EVAL_VOTES = int(os.environ.get("MTA_EVAL_VOTES", "3"))
JUDGE_MODEL = os.environ.get("MTA_JUDGE_MODEL", "claude-opus-5-5")
JUDGE_EFFORT = os.environ.get("MTA_JUDGE_EFFORT", "high")

# Held-out series: never used for prompt/threshold tuning (see README).
SC_SERIES = {"ES2004", "TS3007"}

# Agent limits (enforced in code, not by the model).
MAX_CALLS_PER_ITEM = 4
MAX_CALLS_PER_MEETING = 30
MAX_INVALID_ACTIONS = 2
MAX_NO_PROGRESS = 2
MAX_FINISH_TRIES = 3
# Hard stop on model turns per item, whatever the model returns (normal worst case is about 9).
MAX_AGENT_STEPS = 12
P2_ENABLED = os.environ.get("MTA_P2_ENABLED", "1") != "0"

# Whole-meeting limits. Every model attempt, including every kind of retry, shares these.
MAX_MODEL_CALLS = int(os.environ.get("MTA_MAX_MODEL_CALLS", "80"))
MAX_TOTAL_TOKENS = int(os.environ.get("MTA_MAX_TOTAL_TOKENS", "1000000"))
REQUEST_TIMEOUT = float(os.environ.get("MTA_REQUEST_TIMEOUT", "300"))
MEETING_TIMEOUT = float(os.environ.get("MTA_MEETING_TIMEOUT", "1200"))
MAX_INFLIGHT = int(os.environ.get("MTA_MAX_INFLIGHT", os.environ.get("MTA_WORKERS", "4")))
TRANSPORT_RETRIES = int(os.environ.get("MTA_TRANSPORT_RETRIES", "2"))
RETRY_BACKOFF = float(os.environ.get("MTA_RETRY_BACKOFF", "0.5"))
INPUT_TOKEN_PADDING = 1024  # conservative UTF-8 request-byte bound plus framing allowance

# Speed.  WORKERS: parallel model calls within one meeting (chunk extraction, agent items, abstracts); 1 = sequential.
WORKERS = int(os.environ.get("MTA_WORKERS", "4"))
# Whole-meeting extraction passes that return no usable item are asked again this many times.
EMPTY_RETRIES = 2
# Recall/stability by sampling: the action pass runs ACTION_SAMPLES times (results united); consolidation runs
# CONSOLIDATE_SAMPLES times and keeps the items a majority of the runs agree on.
ACTION_SAMPLES = int(os.environ.get("MTA_ACTION_SAMPLES", "2"))
CONSOLIDATE_SAMPLES = int(os.environ.get("MTA_CONSOLIDATE_SAMPLES", "3"))
DECISION_RECONSOLIDATE = os.environ.get("MTA_DECISION_RECONSOLIDATE", "1") != "0"


def _effort(name: str, default: str | None) -> str | None:
    """Reasoning effort per call type (OpenAI reasoning models only): minimal / low / medium / high.
    None or an empty env value leaves the model default (medium)."""
    return os.environ.get(f"MTA_EFFORT_{name}", default) or None


EFFORT_EXTRACT = _effort("EXTRACT", None)      # finding items is the step that most needs reasoning
# Not lowered: on TS3006, "low" kept about twice as many items and relabelled decisions as actions (F1 roughly halved).
EFFORT_CONSOLIDATE = _effort("CONSOLIDATE", None)
EFFORT_AGENT = _effort("AGENT", "low")
EFFORT_ABSTRACT = _effort("ABSTRACT", "low")

# Upper bound per type after consolidation (gold on the 16 development meetings: actions 1-5, decisions 0-9,
# problems 0-5 per meeting; means 3.0 / 4.6 / 2.4).
MAX_ITEMS = {"actions": 5, "decisions": 7, "problems": 4}

# Extraction chunking.
CHUNK = 150
OVERLAP = 20

# POSSIBLE_PRIOR flag: content-word Jaccard against earlier system items (calibrated on SB gold).
PRIOR_JACCARD = 0.08

