# Handoff: MVP state, protocol for tuning, and how to run the held-out test

## State
The MVP runs end to end on any meeting: TXT → parser → extraction → consolidation → normalization → triage →
follow-up agent (retrieval tools, limits in code, decision log) → validation → report + JSON (gold schema).
Evaluation scripts exist for gold comparison and evidence support. **The held-out series (ES2004, TS3007) have not been run.**
Development results (TS3006 only) are in `docs/METHOD_EN.md` section 4.1; they are weak on actions and show no clear gain
from earlier-meeting context. The model available (gpt-5-nano via Portkey) is small and not deterministic.

## Commands (Windows, from the project root)
```
set PYTHONPATH=src
.venv\Scripts\python -m pytest tests -q                                   # offline tests, no API calls
.venv\Scripts\python scripts\run_series.py TS3006 s2 mvp                   # a->d in order; modes: b0, s1, s2; output runs\mvp\<mode>\
.venv\Scripts\python -m mta.evaluate TS3006 --run runs\mvp --mode s2 --judge   # gold comparison (LLM alignment)
.venv\Scripts\python -m mta.support TS3006 --run runs\mvp --mode s2        # evidence support + 30-item sample for hand checking
.venv\Scripts\python scripts\demo.py TS3006c                               # builds docs\DEMO_TS3006c.md from real outputs
```
`.env` must contain `OPENAI_API_KEY` (Portkey key); `OPENAI_MODEL` and `OPENAI_BASE_URL` are optional overrides.

## Tuning protocol (the part that protects the test result)
1. Tune only on the development series: ES2011, IS1008, TS3004, TS3006. Never open SC transcripts, gold, or results.
2. The few-shot examples in the prompts come from ES2011b, IS1008b, TS3004c (see `docs/FEWSHOT_EXAMPLES.md`); exclude those items when evaluating those meetings.
3. After any prompt, threshold or code change, delete `runs/_cache` (extraction and consolidation are cached by prompt + model; a changed prompt invalidates them automatically, but a clean cache avoids confusion).
4. When satisfied, **freeze** (commit, note the commit hash), then run B0, S1, S2 on SC once:
   ```
   .venv\Scripts\python -m mta.run ES2004 --mode b0 --allow-sc --out runs\final
   .venv\Scripts\python -m mta.run ES2004 --mode s1 --allow-sc --out runs\final
   .venv\Scripts\python -m mta.run ES2004 --mode s2 --allow-sc --out runs\final
   ```
   and the same for TS3007. `mta.evaluate` and `mta.support` refuse SC series; to score SC, remove the guard in a separate commit
   **after** the runs exist, and say so in the report.
5. After seeing SC results: only mechanical checks (blocked meetings, missing files). Changing the system and re-running SC
   makes the number non-clean; if you do it anyway, report both runs and label the second as post-hoc.

## Known issues and suggested next steps (in order of value)
- **Run-to-run variance is large** (same input, different item counts). Consolidated items are cached so S1 and S2 are compared on the same items, but B0 is not.
  Repeat runs and report the range, or compare on several series.
- **Tune on more than one series.** Everything was tuned on TS3006 plus statistics from the 16 development gold files. Run ES2011/IS1008/TS3004 and check nothing collapses.
- **Actions are the weak point.** Ideas: per-role action pass (ask for PM, ID, UI, ME separately on the last 40% of the meeting); cap items per type by evidence size.
- **Earlier-meeting links are not recovered** (0 true positives on TS3006). Check `get_prior_items` queries in the traces; the agent often searches with long, noisy queries.
- **S2 owners are less supported by cited text than S1's** (7/15 vs 12/14, tiny sample). Check by hand with `support_sample_*.md`.
- The agent is offered `get_prior_items` even for the first meeting of a series; it wastes calls (no effect on results). Hide the tool when meeting order is 1.
- Evidence-support judging is an LLM judgement and lenient; do the manual check of the 30-item sample and report agreement.
- Gold: the README in the gold repository still says "unreviewed drafts" although files are filled in; confirm the final status before the SC run.

## Files
- `src/mta/`: `pipeline.py` (flow), `extract.py`, `triage.py`, `agent.py`, `store.py` (retrieval + visibility rules), `validate.py`, `render.py`, `evaluate.py`, `support.py`, `prompts.py`, `config.py`.
- `data/`: transcripts (copied from the gold repository), `participants.json` (speaker → participant/role per meeting), `scenario_text.txt`.
- `schemas/output_schema.json`: gold schema 0.5.0 (copied).
- `docs/`: method (English), this handoff, demo, few-shot list. `ARCHITECTURE_V4.md` / `ARCHITECTURE_GUIDE.md` are the earlier Chinese design notes (the code differs in places: action pass over the whole meeting, deterministic repair, derived `prior_context_needed`).
