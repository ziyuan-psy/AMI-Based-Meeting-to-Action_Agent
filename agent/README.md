# Meeting-to-Action agent

TXT transcript -> parser -> extraction -> triage -> adaptive follow-up agent (retrieval tools, code-enforced limits,
decision log) -> validation -> readable report + JSON in the revised-gold schema (0.5.0).

Method (English): `docs/METHOD_EN.md`. Handoff, run commands and the tuning/test protocol: `docs/HANDOFF.md`.
Earlier design notes (Chinese): `ARCHITECTURE_V4.md`, `ARCHITECTURE_GUIDE.md`.
Item retention, atomic Decision selection and bounded P2 revision: [`docs/ITEM_QUALITY_REPAIR.md`](docs/ITEM_QUALITY_REPAIR.md).

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

Consolidation items also retain `candidate_indexes`. A separate `decision_consolidate` pass uses the same
candidates and three additional samples, then replaces only Decisions. Its prompt preserves independent choices;
its `decision_vote` event records representatives, supporting samples/groups and suppressed broad duplicates.
Action/problem results stay with the original pass. A broad group can support multiple observed narrower choices,
once per sample and choice; this measures candidate inclusion, not independent evidence support. Old standalone
caches without lineage are labeled `decision_vote_fallback`. Cache keys distinguish the prompts/policy; all type
caps remain unchanged. `MTA_DECISION_RECONSOLIDATE=0` disables this pass for the legacy ablation (default is on).
The additional samples/retries share the existing meeting budgets and deadlines.

See [Decision recall diagnosis and paired comparison](docs/DECISION_RECALL.md) for fixed inputs, source snapshots,
all three trial results, remaining misses and the unchanged Action/Issue control. This is development analysis.

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

## Meeting budgets and failure recovery

Each meeting shares one model-call/token budget and deadline across extraction, consolidation, agent
follow-up and abstracts, including retries and parallel requests. Defaults are 80 model attempts,
1,000,000 total input/output tokens, 300 seconds per request, 1,200 seconds per meeting and up to
4 simultaneous model requests. Environment overrides, accounting rules and failure codes are documented
in [Runtime control](docs/RUNTIME_CONTROL.md).

Completed stages are checkpointed under `runs/<mode>/_attempts/<meeting>/<run_id>/`. Failed, timed-out,
cancelled or budget-exhausted runs retain their checkpoints and traces, with a `partial.json` index;
only validated, completed runs publish the final record/report. The latest `.run.json` records the
failure stage, stop reason and per-attempt budget ledger. The series runner stops on failure and skips
only prior successful record/report pairs whose hashes still match. The frozen development baseline
above is unchanged; budget-control regression tests use offline transports.

## P2 checks and clarification requests

[Scenario passage retrieval and P2 citations](docs/SCENARIO_CONTEXT.md) describes `read_project_doc`, explicit
document assessment, source/range validation and citation retention under the existing budgets.

Cross-meeting retrieval, per-link evidence witnesses, time boundaries and the real development demonstration
are documented in [Cross-meeting linking](docs/CROSS_MEETING.md).
The [TS3006a → TS3006b case](results/cross_meeting/CASE.md) includes a fresh live run, actual source evidence,
a separate human confirmation, a missing-record ablation and all development attempts.

S1/S2 now check every item for statement support, owner/deadline ambiguity, end-of-meeting status, conflicts
and historical relationships. The controller drives accept, retrieval, changed searches or pending review from
those findings; it rejects unread/inexact quotes and prevents `finish` from bypassing unresolved checks.
Queries and lookup choice remain the agent's. All model turns share the meeting's existing budget.

Runs also publish `<meeting>.p2.json` and `<meeting>.clarifications.json`, with checked fields, actual actions,
termination reasons and questions linked to item/record IDs and evidence. Unknown owner/deadline values are
cleared; unsupported statements are quarantined from both the record and abstract. Questions appear in the report.
Pending items are explicitly distinguished from accepted items. `MTA_P2_ENABLED=0` restores the legacy ablation;
B0 keeps its original behavior. The output record schema and UI remain unchanged.

See [P2 control and output contract](docs/P2_CONTROL.md) for schemas, state transitions, partial artifacts,
source constraints and the distinction between automated checks and independent human evaluation.

Evaluation (`mta.evaluate --judge`, `mta.support`, `scripts/full_eval.py`) uses the pipeline model as the LLM judge.
Optional: `MTA_JUDGE=claude` switches the judge to Claude (`claude-opus-5-5`, effort `high`; override with
`MTA_JUDGE_MODEL`, `MTA_JUDGE_EFFORT`); this needs `anthropic` and `ANTHROPIC_API_KEY`, and keeps separate caches.

Speed settings (environment variables, optional):
- `MTA_WORKERS` (default 4): stage workers inside one meeting (extraction chunks, agent items, abstracts).
  `1` = sequential; `MTA_MAX_INFLIGHT` also limits simultaneous model requests across these workers.
- `MTA_EFFORT_EXTRACT`, `MTA_EFFORT_CONSOLIDATE`, `MTA_EFFORT_AGENT`, `MTA_EFFORT_ABSTRACT`: reasoning effort for
  OpenAI reasoning models (`minimal` / `low` / `medium` / `high`; empty = model default). Defaults: agent and abstract
  `low`, extraction and consolidation model default. Consolidation at `low` roughly halved item F1 on TS3006.

For a paired check against the frozen teammate branch, see [development regression](docs/DEVELOPMENT_REGRESSION.md).
It records fresh SB runs, three-vote alignment, P2 quarantine and actual usage; it is separate from final evaluation.

P2 uses a flat evidence-ID protocol, separates pending fields from supported statements, and reserves first
checks and summary attempts inside the original meeting budget. See [the protocol repair](docs/P2_PROTOCOL_REPAIR.md).

## Tests (no API calls)
```
set PYTHONPATH=src
.venv\Scripts\python -m pytest tests -q
```
