# B0 baseline report: one-shot extraction

## 1. What B0 is
B0 is the reference system the workflow is compared against. It answers: *how much do chunking, consolidation, triage, the follow-up agent and earlier-meeting context add over asking the model once?*

- **Input:** the whole meeting transcript (after the repository parser and the participant-ID step), in one prompt.
- **One extraction call:** the model lists actions, decisions and problems in one pass, with owner, deadline, status and the line numbers that support each item. Line numbers are mapped back to real dialogue-act IDs in code.
- **Same instructions as the full system:** B0 receives the same output field template, annotation rules and ten example items (from development series other than TS3006) as the workflow's extraction step. It is therefore a fairly strong baseline, not a naive prompt.
- **What B0 does not have:** no chunking, no consolidation pass, no triage, no follow-up agent, no retrieval tools, no earlier meetings (so `related_items` is always empty and `prior_context_needed` always false).
- **Shared with the workflow:** code-side normalization (participant IDs, "we"/whole-roster owners marked ambiguous, deadline wording kept), schema validation with one rule-based repair, one short model call for the summary sentences, and the same report renderer.

## 2. Setup
- Data: development series TS3006, meetings a–d (gold: 13 actions, 22 decisions, 12 problems in total).
- Four independent runs: the first run (`runs/mvp/b0`) and three repetitions (`runs/reps/r1..r3/b0`), each with its own cache.
- Scoring: `mta.evaluate --judge` (LLM alignment of system items to gold items; one-to-one) and `mta.support` (evidence support, first run only).
- The model is a small reasoning model and is not deterministic, so repeated runs on the same input differ.

## 3. Results (TS3006, four runs)

| Run | Actions P / R / F1 | Decisions F1 | Problems F1 | Items listed (actions / decisions / problems) | Owner exact |
|---|---|---|---|---|---|
| 1 (first) | 0.20 / 0.62 / 0.30 | 0.53 | 0.35 | 41 / 23 / 22 | 4/8 |
| 2 | 0.17 / 0.69 / 0.28 | 0.20 | 0.33 | 52 / 8 / 6 | 6/9 |
| 3 | 0.19 / 0.46 / 0.27 | 0.11 | 0.31 | 31 / 14 / 14 | 4/6 |
| 4 | 0.19 / 0.77 / 0.30 | 0.46 | 0.49 | 54 / 22 / 21 | 6/10 |
| **Mean (min–max)** | 0.19 / 0.63 / 0.29 (F1 0.27–0.30) | **0.32 (0.11–0.53)** | 0.37 (0.31–0.49) | 44.5 / 16.8 / 15.8 | |

Pooled over the four runs (counts added up; the same 13/22/12 gold items are counted in each run):

| | Matched | System items | Gold items | Precision | Recall |
|---|---|---|---|---|---|
| Actions | 33 | 178 | 52 | 0.19 | 0.63 |
| Decisions | 27 | 67 | 88 | 0.40 | 0.31 |
| Problems | 21 | 63 | 48 | 0.33 | 0.44 |

Other measures, pooled over the four runs (on items matched to gold):
- Owner: exact set of participant IDs correct in 20/33 (61%); owner basis label (explicit/inferred/ambiguous/missing) agrees in 29/33 (88%).
- Deadline: wording matches gold (including "no deadline") in 22/33 (67%); a deadline was invented where gold has none in 4/26 cases; deadline basis agrees in 22/33.
- Status: decisions correct in 26/27, problems 7/21, actions 4/33 (the model writes `open`; gold often says `in_progress`).
- Earlier-meeting measures: `related_items` true positives 0 (B0 predicts none; gold has 8–13 per run); `prior_context_needed` correct in 45/81, and 0 of 36 gold-true items detected. These are structural zeros for a system with no access to earlier meetings.

Evidence support (first run only; LLM judge reading only the cited lines; not checked by hand):

| Claim | Supported | Partial | Unsupported |
|---|---|---|---|
| Action statement (n=41) | 29 | 3 | 9 |
| Action owner (n=40) | 32 | 0 | 8 |
| Decision statement (n=23) | 19 | 1 | 3 |
| Problem statement (n=22) | 14 | 5 | 3 |

## 4. What the results show
1. **Actions: over-generation, not misses.** Recall is reasonable (0.63 on average, 0.46–0.77) but precision is about 0.19: B0 lists 31–54 actions per series against 13 in gold, mostly in-meeting steps and small tasks, not the follow-up assignments gold records. F1 is stable (0.27–0.30) because the over-generation is stable.
2. **Decisions: very unstable.** F1 ranges from 0.11 to 0.53. The run with 8 decisions scored 0.20 and the run with 23 scored 0.53, so how many items the model chooses to list drives the score more than how well it extracts. The first run's 0.53 is the best of four; the mean is 0.32. Comparisons that use only that run overstate B0 on decisions.
3. **Problems are moderate and noisy** (0.31–0.49).
4. **Statuses of decisions are right** after the rule that unhedged proposals are treated as adopted (26/27), but action statuses are mostly wrong against gold (`open` vs `in_progress`).
5. **Owners:** about 6 in 10 matched actions have exactly the gold owner set; evidence support for owners is about 80% by the (lenient) judge.
6. **No cross-meeting ability:** by construction, B0 records no links to earlier meetings.

## 5. Limitations
- One development series, four meetings; 13 gold actions. A difference of one or two items moves a metric a lot.
- Prompts, example items and the status rule were set using this series and statistics from the development gold, so these numbers are optimistic for new data; the held-out series (ES2004, TS3007) has not been run.
- Alignment to gold and evidence support are LLM judgements. Alignment was used for all four runs; evidence support only for the first run, and none of it has been spot-checked by hand.
- B0 and the workflow share the prompt, so any change to the prompt changes both; the four-run spread above describes the current prompt only.
- Before the held-out run, B0 should be run once with the frozen configuration alongside the workflow systems; variance means one run is a weak summary, so reporting the range (as here) is recommended.
