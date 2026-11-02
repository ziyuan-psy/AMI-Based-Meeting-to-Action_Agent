# Frozen candidate: evaluator handoff

The developer performs the real development acceptance. The teammate performs the
held-out final evaluation. No held-out TXT, gold or predictions were opened to
prepare this package. Check `66a21d7-v1/acceptance.json` for the final acceptance
verdict; a running state or an automated-only audit is not a release verdict.

## Version and effective strategy

- Runtime reference: `66a21d7c58651ef79c8d9e8f146ebd3880f35385`, branch `agent-development`.
- Explicit profile: **`recall_candidate_v1`**. The compatibility default is
  `development`; omitting the profile selects a different experiment.
- Exact 414a31f extraction/consolidation prompt strings; 2 Action samples and
  3 capped consolidation samples, with evidence-overlap voting.
- Type-aware Action recovery, original Issue hypothesis recovery, stable 414
  ordering and unchanged caps (5 Actions / 7 Decisions / 4 Issues).
- No newer model selector or Decision second pass. Flagged items retain Agent
  search/read/continue/finish decisions and the delivered-dialogue citation guard.
- **Full P2 hard publication checks are disabled.** This includes the full P2
  conflict, historical-change witness and scenario-document validation protocol.
  Retaining parser, budgets and trace does not mean this profile runs all P2.
- Model: `@30875-fall26-openai/gpt-5-nano`, OpenAI-compatible Portkey endpoint
  `https://api.portkey.ai/v1`. Do not substitute another model or judge silently.
- Per meeting: 80 attempted model calls, 1,000,000 charged tokens, request timeout
  300 seconds, meeting timeout 1,200 seconds, at most 4 in flight, 2 transport
  retries. Every retry shares these limits. Tool limits are 4/item and 30/meeting.
- Canonical settings, active prompts, code/schema hashes and package versions:
  `66a21d7-v1/study.json`. This file contains no API key.

`MTA_P2_ENABLED=1` is a requested setting, not proof that P2 ran. Always report
the **effective `quality_policy`**. The candidate's report says **Draft candidate**;
empty P2/clarification sidecars do not certify that all items are supported or
that there are no ambiguities. Unresolved Action fields also appear in the report.

## Scope and evidence

Acceptance scope is one **fresh real API** S2 sequence, TS3006a→b→c→d (4 of the
16 development meetings). It opens TXT and uses the native parser, extraction,
merge/vote/recovery/cap, follow-up, summary, validation and publication stages.
It is not a new 16-meeting quality estimate. See `66a21d7-v1/REPORT.md` and
`audit.json` for completion, actual costs, citations and source review.

The earlier 16-meeting result was **fixed-response replay**:

| View | Action F1 | Decision F1 | Issue F1 | Pooled F1 |
|---|---:|---:|---:|---:|
| 414a31f development reference | .474 | .441 | .429 | .449 |
| Candidate postprocessing of recorded responses | .505 | .441 | .457 | .464 |

These are development, model-judged comparisons, not held-out results or fresh
online end-to-end scores. Issue recall improved with more unmatched predictions.
Do not replace this provenance with a claim of final accuracy.

## Before the teammate runs held-out predictions

Receive the source and **this complete acceptance folder**, including the frozen
study, verdict, manifest and Python entry points. The acceptance artifacts are
not automatically available from runtime commit 66a21d7 alone; they were prepared
after that commit. Verify their manifest before transfer/use.

Supply a private Portkey credential via `OPENAI_API_KEY` in the environment or
`agent/.env`. Existing environment values take priority. Never print or commit the
credential. Use the project's installed Python dependencies (see study metadata).
From the repository root in PowerShell:

```powershell
$env:PYTHONPATH = "agent/src"
python agent/results/candidate_acceptance/run_frozen.py --verify-only
```

The preflight does not read held-out inputs or make a model call. It compares
actual source/schema bytes, model, configuration and effective policy. A later
evidence/docs-only descendant commit is allowed only if the runtime hashes still
match; the actual checkout commit and runtime reference are both recorded.

## Fix the final protocol before opening held-out data

The earlier project protocol calls for **B0, S1 and S2** on ES2004 and TS3007
(2 series × 4 meetings × 3 modes = 24 prediction runs). Each mode runs a→d.
Keep this as the proposed final protocol unless the team explicitly records a
different scope before the first held-out run. The evaluation owner signs off
the gold version/status and protocol; development does not inspect the gold here.

Mode-specific behavior at this commit matters: **B0 remains the frozen current
one-shot baseline**, with `quality_policy.candidate=false` and the current
`prompts.EXTRACT_SYSTEM`; it does not use the candidate's copied 414a31f prompt.
S1/S2 use the same candidate content policy. Thus B0→S1 is a comparison of named
configurations, not a pure follow-up-only ablation; **S1↔S2** is the controlled
prior-context comparison. Record all active prompt hashes. If the team requires
an identical-prompt B0 ablation or a 414a31f held-out reference, specify and verify
that evaluation arm on development data before opening held-out inputs. Do not
silently relabel this B0 as the teammate's 414a31f version.

Recommended context ablation: S1 and S2 share extraction/consolidation responses
within the same series, so their differences measure access to prior meetings
rather than a second extraction sample. Use the explicit `shared_s1_s2` policy:

```powershell
python agent/results/candidate_acceptance/run_frozen.py --series ES2004 TS3007 --modes b0 s1 s2 --cache-policy shared_s1_s2 --out agent/runs/final_66a21d7_v1 --allow-heldout
```

**This command was not executed during development acceptance.** The runner
requires the completed acceptance verdict and a new output directory. It assigns
the cache explicitly; changing `mta.run --out` alone does not isolate the global
cache. B0 uses a separate cache. S2 timing with reused extraction is incremental;
report it that way, with S1 cold-run costs alongside it. If the team instead
chooses independent cold costs, fix `cold_each_mode` before running and disclose
that S1/S2 then have separately sampled extraction. Do not run both policies and
choose the better held-out score.

The runner records requests, results, runtime metadata and the selected cache
policy, and stops after a failed meeting. Preserve a failed attempt. Classify
infrastructure/mechanical failure before any allowed retry, use a new attempt
directory, and report both attempts. Do not change prompts, caps, recovery or
P2 policy after seeing held-out results and still call it the same frozen test.

## Scoring and artifacts the evaluation owner must retain

Use the same revised-gold version and scoring protocol as the 16-meeting
comparison: `mta.regression.score_items`, the existing one-to-one LLM matcher
with **3 votes**, the same `FEWSHOT_GOLD` exclusions, and `NA` placeholder
categories excluded from denominators. Save the gold hashes, judge identity,
all vote caches, matched pairs, unmatched predictions/gold and denominators.
Report Action/Decision/Issue P/R/F1 and pooled micro P/R/F1, and owner accuracy
over matched Actions. Do not compare differently excluded or scored populations.

`agent/scripts/full_eval.py` still hardcodes the four development series and
uses older denominator/efficiency handling. `mta.evaluate`/`mta.support` also
have held-out guards. **Do not run those entry points unchanged and label their
output as this final protocol.** The evaluation owner must prepare an evaluation
adapter for the fixed series/protocol without modifying the frozen runtime or
exposing gold to it; use the existing scoring functions directly. Verify that
adapter on development artifacts first, before opening held-out gold.

Preserve these for each meeting/mode:

- JSON, Markdown report, `.run.json`, `.stages.jsonl`, `.trace.jsonl`, request
  trace, checkpoints, `.p2.json` and `.clarifications.json`, plus partial artifacts
  on failure. Check hashes and terminal status before scoring.
- `runtime_control.attempted_calls`, charged and unknown-usage tokens, retries,
  peak concurrency, failure reason and actual elapsed time. Separate prediction
  costs from judge costs; do not use the old lower-bound `_timing` estimate.
- Evidence-support judgments and a fixed human-reviewed sample, summary
  faithfulness/coverage if included, and cross-meeting relation judgments with
  old/current supporting dialogue. Distinguish valid source/scope from semantic
  support; delivered citations alone do not prove the statement or relation.

The source/scope guard allows only the current meeting and earlier meetings of
the same series. No development history should be seeded into held-out S2.
Report zero links, missing fields and failures honestly; do not regenerate for
more favorable examples. The development acceptance does not certify all
conflicts, qualifiers, role inferences, document citations or historical links.

## Local status

This handoff prepares evaluation; it does not execute it or send messages to the
teammate. UI, annotation documents and existing experiments are preserved.
No commit or push is included in this acceptance task.
