# Meeting-to-Action agent

TXT transcript -> parser -> extraction -> triage -> adaptive follow-up agent (retrieval tools, code-enforced limits,
decision log) -> validation -> readable report + JSON in the revised-gold schema (0.5.0).

Method (English): `docs/METHOD_EN.md`. Handoff, run commands and the tuning/test protocol: `docs/HANDOFF.md`.
Earlier design notes (Chinese): `ARCHITECTURE_V4.md`, `ARCHITECTURE_GUIDE.md`.

## Setup (once)
```
py -V:Astral/CPython3.13.7 -m venv .venv
.venv\Scripts\python -m pip install anthropic openai jsonschema pytest
# put OPENAI_API_KEY=... in .env (git-ignored); optional OPENAI_MODEL, OPENAI_BASE_URL (defaults: Portkey gpt-5-nano)
# Anthropic is used instead if only ANTHROPIC_API_KEY is set (MTA_MODEL optional)
.venv\Scripts\python scripts\prepare_data.py --ami-dir "<...>\ami-corpus\ami-corpus"   # already done: data/participants.json
```

## Run
```
set PYTHONPATH=src
.venv\Scripts\python -m mta.run TS3006 --mode s2          # a->b->c->d, later meetings read earlier output
.venv\Scripts\python -m mta.run data\transcripts\TS3006b.txt --mode s2
.venv\Scripts\python -m mta.run TS3006 --mode s1          # ablation: no earlier-meeting context
.venv\Scripts\python -m mta.run TS3006 --mode b0          # one-shot baseline, same schema, no tools
```
Outputs go to `runs/<mode>/`: `<meeting>.json`, `<meeting>_report.md`, `<meeting>.trace.jsonl`.
Held-out series (ES2004, TS3007) are refused unless `--allow-sc`; use it once, after the design is frozen.

Evaluation (`mta.evaluate --judge`, `mta.support`, `scripts/full_eval.py`) uses the pipeline model as the LLM judge.
Optional: `MTA_JUDGE=claude` switches the judge to Claude (`claude-opus-5-5`, effort `high`; override with
`MTA_JUDGE_MODEL`, `MTA_JUDGE_EFFORT`); this needs `anthropic` and `ANTHROPIC_API_KEY`, and keeps separate caches.

Speed settings (environment variables, optional):
- `MTA_WORKERS` (default 4): parallel model calls inside one meeting (extraction chunks, agent items, abstracts). `1` = sequential.
- `MTA_EFFORT_EXTRACT`, `MTA_EFFORT_CONSOLIDATE`, `MTA_EFFORT_AGENT`, `MTA_EFFORT_ABSTRACT`: reasoning effort for
  OpenAI reasoning models (`minimal` / `low` / `medium` / `high`; empty = model default). Defaults: agent and abstract
  `low`, extraction and consolidation model default. Consolidation at `low` roughly halved item F1 on TS3006.

## Tests (no API calls)
```
set PYTHONPATH=src
.venv\Scripts\python -m pytest tests -q
```
