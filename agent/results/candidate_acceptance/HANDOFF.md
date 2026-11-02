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
python agent/results/candidate_acceptance/verify_package.py
python agent/results/candidate_acceptance/run_frozen.py --verify-only
```

The preflight does not read held-out inputs or make a model call. It compares
actual source/schema bytes, model, configuration and effective policy. A later
evidence/docs-only descendant commit is allowed only if the runtime hashes still
match; the actual checkout commit and runtime reference are both recorded.

Before any held-out input is opened, verify the scoring entry point against the
existing **16-meeting development replay** (no model requests):

```powershell
python agent/results/candidate_acceptance/score_frozen.py --saved-dev agent/results/recall_candidate/41654ab-v2 --offline --out agent/runs/scoring_check_66a21d7_v1
```

Expected: all 16 jobs `COMPLETED`, `judge_calls: 0`, and pooled P/R/F1
**0.422 / 0.517 / 0.464**. The script checks every original match/unmatched list
and every type/pooled metric against the saved reference; a mismatch fails the
command. See `plan.json` → `saved_reference_validation` and `REPORT.md` in that
new output directory. This verifies scoring, not a fresh Agent quality result.
Use a new check directory each time; do not write evaluator outputs inside the
transferred acceptance package, whose manifest must remain stable.

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
output as this final protocol.** The ready-to-run adapter is now
**`score_frozen.py` in this folder**. It calls the existing scoring functions
directly and imports the frozen summary helpers without changing the Agent or
the old commands. The scoring rules are saved in `scoring_protocol.json`.

Once the prediction command above has completed, run this separate scoring
command, from the repository root (replace the gold directory if the team keeps
its agreed gold elsewhere):

```powershell
python agent/results/candidate_acceptance/score_frozen.py --run agent/runs/final_66a21d7_v1 --gold revised_gold_output --series ES2004 TS3007 --modes b0 s1 s2 --layout series --semantics all --online --allow-heldout --out agent/runs/final_scores_66a21d7_v1
```

**This held-out command has not been executed during development.** The complete
path is now: package/preflight check → offline development score check → frozen
predictions → this scoring command → human review → final report. Gold is read
by the scorer only; it is never supplied to the Agent or used to regenerate its
predictions. Do not change the runtime after seeing final scores.

`--semantics all` includes the existing evidence-support judgment (one judgment
of each item batch) and summary faithfulness/coverage (one judgment each, using
the original prompts), in addition to three-vote extraction alignment. Support
judging sees the first 10 cited dialogue lines per item, as before; manual review
should inspect more context when needed. These are model judgments, not human
certifications. `--semantics none` requests only extraction, matched-field and
relation metrics plus efficiency; if the team chooses that smaller scope,
record the choice **before** opening held-out data. Do not select the scope
after observing scores. There is no separate gold-summary faithfulness arm.

Scoring is **offline by default**. `--online` explicitly permits new judge
requests with the same accepted Portkey model. Each meeting×mode scoring task
uses the existing controller and unchanged 80-call / 1,000,000-token / 300-second
request / 1,200-second task / 4-in-flight / 2-retry limits. Judge costs are
separate from prediction costs. Calls are sequential by default and never exceed
the configured concurrency ceiling.

Scoring caches are separate from prediction caches. For each meeting, identical
ordered text lists in B0/S1/S2 reuse the same original three votes; different
lists receive their own votes. Caches bind the judge identity, protocol, item
type and complete ordered inputs. Existing votes cannot be replaced or reordered.
To continue after a classified mechanical scoring failure, preserve that whole
attempt and use a **new** output directory, adding:

```powershell
--votes-from agent/runs/final_scores_66a21d7_v1
```

The previous manifest and protocol must verify. Only missing votes are requested;
completed judgments are reused. Do not retry for a better matching outcome. The
new attempt has its own unchanged task limits; retain both attempts and include
both judge costs. There is no automatic retry of a failed scoring campaign.

The scorer refuses an incomplete prediction campaign, changed publication
hashes, wrong model/config/policy, missing same-mode historical records or a
different requested population. It stops after a failed scoring job and marks
the report `INCOMPLETE`; no headline F1 is reported for an incomplete mode.
`--check-only` runs the input/publication checks without requesting judgments.
An example using the four already completed real development meetings is:

```powershell
python agent/results/candidate_acceptance/score_frozen.py --run agent/results/candidate_acceptance/66a21d7-v1/run --gold revised_gold_output --series TS3006 --modes s2 --layout native --check-only --out agent/runs/native_scoring_preflight_v1
```

Files in the scoring output:

| File/folder | Contents |
|---|---|
| `REPORT.md`, `summary.json` | Mode/type P/R/F1, pooled micro F1, matched-owner/basis/deadline/status accuracy, relation diagnostic, support/summary scores if requested, and efficiency |
| `plan.json` | Fixed protocol, selected population, input/gold/metadata hashes, cache policy and overall status |
| `inputs/` | Exact predictions and gold that were scored |
| `per_meeting/` | Counts, matched/unmatched records, excluded items, raw alignments, requested semantic results, failures, budget and usage |
| `votes/`, `traces/` | Actual vote/semantic caches, judge requests, returned tool payloads and controller accounting |
| `human_review_template.json`, `.md` | Fixed 30-item sample with blank human columns; fill a separate copy and retain the untouched template |
| `manifest.json` | Output integrity hashes |

Owner/field accuracy is over scored matched items. The relation diagnostic uses
matched non-abstract source items and maps targets through all same-mode meeting
alignments; unmatched targets count as unmatched predictions. It is separate
from extraction F1 and is not full-population relation recall. Generation costs
come from `.run.json` runtime accounting, including unknown-usage reservations;
recorded replay without native cost data is marked unavailable, not zero-cost.

The development verification is recorded in `scoring_validation/v1/REPORT.md`:
31 offline tests, exact 16-meeting score reproduction, checked cache reuse, and
the four-meeting native-output preflight. These checks make no real API calls
and do not open held-out files.

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
