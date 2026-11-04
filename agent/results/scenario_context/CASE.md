# Scenario document evidence: development step 7

This bundle demonstrates passage retrieval and preservation of explicitly assessed scenario citations through
P2's final output. It contains live model calls on genuine TS3006d development input, source snapshots,
bounded-reader verification and unsuccessful attempts. It is a focused Agent-stage development check, not
an extraction benchmark or final evaluation. No gold files or evaluation judge were used by the runtime.

## Positive live case: LCD and advanced chip

The source is `agent/results/mvp/s2/TS3006d.json`, item `TS3006d.sys.s.1`. The case isolates its first clause:

> The LCD screen will be kept and will include an advanced chip-on-print.

The complete compound source record is preserved in [input.json](scenario-v5/input.json). The omitted second
clause about one large LCD is outside this focused check. The caller explicitly requests scenario verification;
it supplies no expected query, page, passage ID, citation or correct assessment. This demonstrates Agent-selected
retrieval in response to that request, not spontaneous document use in an ordinary full-pipeline run.

The live Agent in [scenario-v5](scenario-v5/run.json) did the following:

1. Assessed the current statement, then chose `search_project_doc` with query
   `LCD screen curved display advanced chip-on-print AMI scenario`.
2. Received three passages: two on physical PDF page 66 and a budget-table passage on page 87.
3. Selected `scenario:p66:1569-1842` as `supports` and `scenario:p66:771-1719` as `context`, explaining that
   scenario instructions connect the display to an advanced chip. The page-87 hit was not selected or cited.
4. Passed the required checks using current-meeting dialogue, then published a decision with both selected
   document ranges retained alongside dialogue references. Code checked source hash, range, text and actual
   tool delivery; it did not choose the semantic relevance judgment for the model.

The end of page 66 says that the display requires an advanced chip. This relevant text is beyond the old
500-character page-prefix excerpt. Page metadata identifies the Industrial Designer instructions at 3:00:00.
That metadata is context, not proof of when an AMI participant actually received the instruction.

Source inspection of the meeting shows `TS3006d.B.dialog-act.dharshi.32` (keeping the LCD),
`TS3006d.B.dialog-act.dharshi.62` (advanced chip-on-print), and the initial evidence neighborhood around
`TS3006d.D.dialog-act.dharshi.144` (asking about the advanced chip needed for the display) and
`TS3006d.A.dialog-act.dharshi.164` (affirmation). The published record cites .32, .62 and .164; the P2 sidecar
preserves the checked quotes. This is Codex source inspection, not an independent human review or a gold label.
Scenario text supplies design context; the meeting evidence supplies the actual choice.

Inspect the artifacts:

- [Final record with two document references](scenario-v5/TS3006d.json)
- [P2 checks, original passage witnesses and selection reasons](scenario-v5/TS3006d.p2.json)
- [Rendered evidence report](scenario-v5/TS3006d_report.md)
- [Actual tool inputs and returned text](scenario-v5/agent.trace.jsonl)
- [Model responses and usage by stage](scenario-v5/stages.jsonl)
- [Code, model, configuration, source hashes and runtime accounting](scenario-v5/run.json)

This run used 3 model calls, 1 lookup and 23,876 reported tokens (16,951 input + 6,925 output), taking about
62.61 seconds including setup. It accepted the statement and retained two ranges on one document page.
It did **not** call `read_project_doc`: the returned search passages were sufficient for its decision.

## Bounded page reading is verified separately

[reader_validation.json](reader_validation.json) records deterministic calls to the real `read_project_doc`
dispatcher against the same scenario page: ranges 0-1800 and 1800-1842 reconstruct all 1,842 characters exactly.
This used zero API calls. It verifies the implemented reader and continuation offsets, not a live model choice
to read the page. A controller regression also drives an Agent through search, insufficient context, reading
more, reassessment and citation preservation using scripted tool decisions.

## Pending results and failed attempts remain visible

[DEVELOPMENT.md](DEVELOPMENT.md) lists every live attempt, including the successful case, later pending runs,
and two HTTP-400 failures during a schema experiment. These are different development versions and are not a
success-rate denominator. Every run keeps the exact runtime sources and configuration used.

The final-code LCD repeat (`scenario-v7`) remained pending. The Agent questioned consensus around the chip,
repeated an unhelpful lookup and stopped at the no-progress limit. No record or document citation was published.
Its existence limits any claim that the live model behavior is stable.

The final-code evaluation case (`evaluation-v2`) retrieved page 83, noticed seven-point-scale instructions
versus a saved candidate describing two-point/majority voting, and selected `contradicts`. It also incorrectly
used a document ID as a dialogue ID; hard validation rejected the proposed fields. It stopped pending with no
published record. Its final failure is therefore a protocol-namespace error as well as an identified contextual
difference, not a clean demonstration of semantic conflict resolution alone. Inspection of the saved current
quotes (including “I agree on two”) does not establish a two-point scale. Extraction repair of this compound
candidate is outside this change.

The root-level conditional schema that caused the two HTTP-400 failures was removed. The final flat object
schema was accepted in subsequent live calls. Unknown provider usage on failed calls is conservatively charged
by the existing budget policy and is reported separately from known token usage.

## Final-code verification and reproduction

The successful `scenario-v5` predates the last controller adjustments. Its original provider responses, plus
the final-code `scenario-v7` and `evaluation-v2` responses, are replayed through the current production tools,
controller, assembler and validators. All three reproduce the saved records and expected accepted/pending
states. This is deterministic replay, not another successful live API run. Snapshot differences and artifact
integrity checks are recorded in [verification.json](verification.json).

The full regression suite passes **226 tests and 11 subtests**. Tests cover late-page retrieval, exact ranges,
source and delivery tampering, unselected hits, pending contradictions, mandatory meeting evidence, citation
retention, cache invalidation, shared budgets, checkpoints, renderer output and real response replay.

From the repository root in PowerShell, with the project's usual runtime dependencies available:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location).Path 'agent/src')
& '.\.venv-ui\Scripts\python.exe' -X utf8 agent/scripts/scenario_demo.py agent/results/scenario_context/new-lcd-run --case lcd
& '.\.venv-ui\Scripts\python.exe' -X utf8 agent/scripts/scenario_demo.py agent/results/scenario_context/new-evaluation-run --case evaluation
```

The script requires a **new** destination and the existing API credentials; it never overwrites captures.
Results may differ, including a pending stop. Existing limits remain 80 model attempts, 1,000,000 tokens,
300 seconds/request, 1,200 seconds/meeting, 4 lookups/item, 30 lookups/meeting and 12 model turns/item. Retries,
concurrency and reassessments share the same run control. UI and annotation files are unchanged.

The scenario retriever still searches the prepared document globally. Time/Role metadata is exposed, but no
new scenario-stage access policy is claimed. Current-meeting evidence and same-series, earlier-meeting history
restrictions remain in place. This bundle establishes the new evidence path and its practical limits; overall
quality still requires the team's final evaluation.
