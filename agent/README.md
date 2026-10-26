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

## Shared transcript parser

The only parser implementation is [`../transcript_parser.py`](../transcript_parser.py).
`src/mta/transcript_parser.py` forwards its public functions, exceptions and CLI to that module;
existing agent commands with `PYTHONPATH=src` still work from `agent/`. Keep the full repository
checkout available when running the agent. Parser fixes belong in the root module only.

Parsing returns the dialogue-act records directly in memory. Writing an intermediate JSON file
is optional. The root parser CLI remains available; the compatibility CLI can also be used from
`agent/` (PowerShell):

```powershell
$env:PYTHONPATH = "src"
python -m mta.transcript_parser --help
```

[`../scripts/export_transcripts.py`](../scripts/export_transcripts.py) stays a separate data-preparation
tool: it converts original AMI dialogue-act JSON to TXT. Participant/role enrichment remains a
separate step after parsing. Output paths and annotation-write protections use the repository root.

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

## Stage trace and development baselines

Every `run_meeting` call now also writes two sidecar files (the item schema is unchanged):

- `<meeting>.stages.jsonl`: raw extraction attempts and rejected candidates; final candidate order;
  each consolidation attempt's raw groups, member-index lineage, merged items and per-type caps;
  majority-vote clusters and counts; restored actions and final caps; normalization, triage and validation.
- `<meeting>.run.json`: unique run ID, actual model/client, effective settings, prompt text/hashes,
  Git commit/branch and hashes of runtime source (including uncommitted files), input/context hashes,
  cache location, final status and per-meeting usage. Credentials and `.env` files are excluded.

The original `.trace.jsonl` remains the agent's tool-decision log for existing consumers. Stage snapshots
are deep copies, so later normalization/triage does not rewrite earlier evidence. Candidate `members`
indexes refer to the ordered candidates in the `extract/completed` event. Each `consolidate/attempt`
contains nested `raw_groups`, `merged` and `cap` events; `merged.lineage` maps groups back to candidate indexes.
The `consolidate/vote` event explains which clusters reached the majority threshold; `cap.dropped`
shows the items removed by quantity limits. `assemble.item_record_map` links agent item keys to final IDs.

Cache hits are explicitly labeled. New consolidation caches preserve their recorded intermediate events;
older caches still work but report `detail_available: false` when pre-cap/group information was not saved.
Those missing historical details cannot be reconstructed from the already-capped cached items.

To save a new SB development baseline, run from `agent/` (PowerShell):

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m mta.baseline --out results/dev_baselines/trace-v1 --meetings TS3006a TS3006b --modes s1 s2
```

This calls the configured model. The destination must be new; earlier captures are never overwritten.
The bundle contains `BASELINE.md`, `baseline.json`, all meeting artifacts and local caches, plus a `source/`
snapshot of runtime code and selected inputs/context. Its manifest hashes every saved artifact. Model names
and a Git hash alone do not identify uncommitted source; use the source digest and captured files as well.
For S2, all earlier meetings must be selected so the context comes from that baseline's own system outputs.
Only SB series are accepted, and no gold labels or evaluation judges are used.

The cache starts cold and is shared across modes inside the bundle so S1/S2 see the same extracted items.
Consequently, S2 latency can include cache reuse; consult each run's stage trace/usage rather than treating
these timings as cold end-to-end comparisons. Final quality evaluation remains a separate task.

Saved capture: [TS3006a/b trace baseline](results/dev_baselines/trace-v1-ts3006ab/BASELINE.md),
with real S1/S2 outputs, source/input snapshots and checksummed artifacts.

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
