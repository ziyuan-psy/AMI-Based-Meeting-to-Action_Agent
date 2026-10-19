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

## Tests (no API calls)
```
set PYTHONPATH=src
.venv\Scripts\python -m pytest tests -q
```
