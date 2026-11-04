# Meeting-to-Action Workflow: Method (English draft)

Sections marked **TODO** are not done yet; numbers come only from real runs. Nothing below reports a number that has not been measured.

## 1. Task and data

The system turns a meeting transcript (a `.txt` file) into a readable record of **decisions, action items and problems**, with owners, deadlines, status, links to earlier meetings, and evidence. It must resolve or flag ambiguous owners and deadlines, and check items against project context.

- **Corpus:** AMI scenario meetings. Six series, 24 meetings (a–d in each series): development series ES2011, IS1008, TS3004, TS3006 (16 meetings); held-out series ES2004 and TS3007 (8 meetings).
- **Gold:** manually revised annotations in schema 0.5.0 (`revised_gold_output`). Each item has `type`, `reference`, and for actions `owner`, `owner_basis`, `deadline`, `deadline_basis`; for actions/decisions/problems also `status`, `prior_context_needed`, `related_items`.
- **Rules that shape the design:** within a series, a later meeting may read earlier meetings; nothing may read later meetings or other series; gold never enters the runtime; held-out series are not used for any tuning.
- **Input** is plain text, one dialogue act per line: `[start --> end] speaker (dialogue_act_id): text`. The repository parser converts it to JSON. Speaker letters (A/B/C/D) are not stable across meetings, so participant IDs and roles are added from a per-meeting table built from the corpus metadata.

## 2. Architecture

Control pattern (required by the assignment): extraction → normalization → ambiguity check → context verification → structured output. The fixed backbone is kept; the ambiguity check and context verification are driven by an **adaptive agent**.

```
TXT → Parse (code) → Add participants (code) → Extract candidates (LLM)
    → Consolidate (LLM) → Normalize (code) → Triage (code)
    → [flagged items only] Follow-up agent (LLM chooses tools; code enforces limits) ⇄ Retrieval tools
    → Assemble (code + LLM abstract) → Validate (code) → Repair once (code)
    → Outputs: report.md, record.json (gold schema), trace.jsonl
```

### 2.1 Extraction
- **Decisions and problems** are local, so they are extracted from overlapping chunks (150 dialogue acts, 20 overlap).
- **Actions** are global (assignments refer to the whole discussion and are often settled late), so they are extracted in one pass over the whole meeting. This was changed after a one-shot baseline outperformed chunked extraction on actions.
- Each candidate must cite real dialogue-act IDs from the transcript; candidates without valid evidence are dropped. The model returns short line numbers, which code maps to IDs, so evidence IDs cannot be invented.
- A consolidation call keeps substantive project-level items and merges duplicates. It retries up to three times and never silently falls back to the raw candidates.
- The extraction prompt contains the output field template (field meanings, enums, required fields per type) and the annotation rules taken from the gold guidelines.

### 2.2 Normalization and triage (code)
- Owners are mapped to participant IDs; "we", "someone", or the whole roster are treated as ambiguous, not as joint ownership. Deadlines keep the original wording; no calendar dates are invented.
- Triage assigns flags: `OWNER_UNRESOLVED`, `JOINT_OWNER_CHECK`, `DEADLINE_CHECK`, `STATUS_CHECK`, `WEAK_EVIDENCE`, and `POSSIBLE_PRIOR`. `POSSIBLE_PRIOR` is raised when content-word overlap with an earlier meeting's item reaches 0.08; this threshold was calibrated on development gold (about two thirds of items flagged, about 83% of items with a gold link covered).
- Unflagged items skip the agent.

### 2.3 Follow-up agent
For each flagged item the model picks one tool call at a time, then calls `finish`. Tools (all read-only):

| Tool | Purpose |
|---|---|
| `search_dialogue(query, scope)` | keyword (BM25) search in the current or earlier meetings of the series |
| `read_dialogue(meeting, line, window)` | read dialogue around a line |
| `get_participants(meeting)` | speaker → participant ID and role |
| `get_prior_items(query, type)` | items the system recorded for earlier meetings |
| `search_project_doc(query)` | AMI scenario documents |
| `finish(...)` | submit owner, deadline, status, related items, evidence |

**Limits enforced in code, not by the model:** tool whitelist; at most 4 lookups per item and 30 per meeting; repeated identical calls return a cached result and count as no progress; stop after two calls without new information or two invalid actions; earlier-meeting access is restricted to the same series and lower meeting order; a `finish` is rejected unless its evidence IDs, participant IDs and related-item IDs exist. If no valid `finish` is produced, the item falls back to the extractor's values and is not guessed further. **Every step is logged** (item, flags, action, arguments, budget left, whether it brought new information, outcome).

Nobody can be asked for clarification in this setting. When owner or deadline stays unclear, the item is recorded as `ambiguous`/`missing` with a null value and listed under "needs human confirmation".

Two fields are decided by code rather than by the model: `prior_context_needed` is true only if the item links to an earlier item or its support comes from an earlier meeting; status is kept from extraction unless the item was flagged `STATUS_CHECK`.

### 2.4 Validation and outputs
- Validation uses the gold JSON schema plus rules: references exist, related items point to earlier recorded items, basis values agree with the values (explicit/inferred need an owner or deadline).
- Failures get one deterministic repair pass; if errors remain, the meeting is marked blocked and the draft is kept for manual review.
- Outputs per meeting: a readable Markdown report (summary, decisions, problems, action table with evidence, changes since earlier meetings, items needing confirmation, evidence appendix), a JSON record in the gold schema, and the agent trace.

## 3. Systems compared
| System | Description |
|---|---|
| **B0** | one-shot baseline: whole transcript in one call, same output schema, no tools, no earlier meetings |
| **S1** | full workflow without earlier-meeting context (earlier transcripts and records are hidden) |
| **S2** | full workflow with earlier-meeting context |

## 4. Evaluation
Implemented in `src/mta/evaluate.py`; run against the gold of the development series only.

- **Alignment:** system items are matched one-to-one to gold items of the same type by an LLM judge (cached); a heuristic matcher is also available.
- **Metrics:** precision/recall/F1 for actions, decisions, problems; owner exact match (set of participant IDs) and owner basis; deadline match and basis, and deadlines invented where gold has none; status accuracy; `prior_context_needed` accuracy and recall on gold-true items; `related_items` precision/recall; dialogue-evidence overlap with gold references.
- **Not yet implemented:** an evidence-*support* judgement (does the cited text actually support the owner/deadline?). The overlap number is only a proxy and must not be read as support.
- **Comparisons:** B0 vs S1 vs S2 (the S1 vs S2 difference is the effect of earlier-meeting context).

### 4.1 Results on the development series (TS3006, one run per system)

Measured with `mta.evaluate --judge` and `mta.support` on `runs/mvp`. **One run each, small sample (4 meetings, 13 gold actions, 22 gold decisions, 12 gold problems); the model is not deterministic, so differences of a few points are within noise.** S1 and S2 start from the same consolidated items, so their difference comes from the agent step only.

| | B0 (one-shot) | S1 (no earlier meetings) | S2 (full) |
|---|---|---|---|
| Actions P / R / F1 | 0.20 / 0.61 / 0.30 | 0.35 / 0.54 / 0.42 | 0.35 / 0.54 / 0.42 |
| Decisions P / R / F1 | 0.52 / 0.55 / 0.53 | 0.53 / 0.36 / 0.43 | 0.60 / 0.41 / 0.49 |
| Problems P / R / F1 | 0.27 / 0.50 / 0.35 | 0.29 / 0.17 / 0.21 | 0.43 / 0.25 / 0.32 |
| Owner exact (matched actions) | 4/8 | 4/7 | 4/7 |
| Deadline match (matched actions) | 5/8 | 5/7 | 5/7 |
| Decision status correct | 12/12 | 8/8 | 9/9 |
| `prior_context_needed` correct | 14/26 | 11/17 | 14/19 |
| `related_items` true positives | 0 of 13 gold | 0 of 8 gold | 0 of 8 gold (2 predicted) |
| Evidence support, action statement (supported) | 29/41 | 14/20 | 13/20 |
| Evidence support, owner (supported) | 32/40 | 12/14 | 7/15 |
| Evidence support, decisions (supported) | 19/23 | 12/15 | 11/14 |

The B0 column is the first of four B0 runs. Over four runs B0's decisions F1 ranged from 0.11 to 0.53 (mean 0.32) and its actions F1 from 0.27 to 0.30; see `docs/B0_REPORT.md`. S1 and S2 were run once, so their own variance is unknown and the first-run B0 numbers above should not be read as typical.

What this shows, and what it does not:
- The multi-step workflow (S1, S2) is more precise on actions than the one-shot baseline (0.35 vs 0.20 precision) but finds fewer decisions and problems.
- **Earlier-meeting context did not clearly help on this series.** S2 vs S1 differences in F1 are small and within run-to-run noise; no gold link between meetings was recovered (`related_items` true positives are 0), and `prior_context_needed` recall on gold-true items was 1 of 6 for S2 and 0 of 6 for S1.
- S2's owners were supported by the cited lines less often than S1's (7/15 vs 12/14). The sample is tiny, but it suggests that owners inferred from earlier items are less well grounded in the cited text. This needs the manual spot-check described in `support_sample_TS3006.md`.
- Evidence support is an LLM judgement and tends to be lenient; none of these support figures have been checked by hand yet.

Held-out series (ES2004, TS3007): not run. They are run once, after the design is frozen — TODO.

## 5. Demonstration
`scripts/demo.py TS3006c` builds `docs/DEMO_TS3006c.md` from the real outputs: the earlier record, the evidence lines, the agent's sequence of choices from the decision log, the S1 result for the same item, and the resulting action table. In the shipped example the agent finds earlier menu/LCD assignments and links the new smartboard task to them (`continues`), but the owner it assigns (ME and UI) is likely wrong, so the demo shows the mechanism, not a correct result.

## 6. Limitations (known so far)
- The model used (a small reasoning model) is weak on extraction; the same input can give noticeably different results across runs, so small differences between systems should not be over-read.
- Prompts and thresholds were tuned on one development series (TS3006) plus statistics from the sixteen development gold files; they were not validated series by series.
- Action extraction is the weakest part: chunked extraction sees too little context, and the whole-meeting pass depends on the model finding the final assignments.
- Alignment to gold is judged by an LLM and is itself imperfect; evidence support is not yet measured.
- The agent is not given a way to ask for clarification, by design.
