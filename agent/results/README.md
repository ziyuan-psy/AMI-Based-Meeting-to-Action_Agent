# Results snapshot (development series TS3006 only)

Copied from `runs/` so teammates can inspect outputs without re-running. The held-out series (ES2004, TS3007) were not run.

- `mvp/b0`, `mvp/s1`, `mvp/s2`: the three systems on TS3006 a-d. Per meeting: `<meeting>.json` (record in the gold schema),
  `<meeting>_report.md` (readable report), `<meeting>.trace.jsonl` (agent decision log; empty for b0),
  `eval_TS3006.json` (comparison with gold), `support_TS3006.json` and `support_sample_TS3006.md` (evidence support and the 30-item sample for hand checking).
- `b0_repeats/r1..r3`: three repetitions of B0 (same layout; `eval_TS3006.json` per run). Together with `mvp/b0` they give the four runs in `docs/B0_REPORT.md`.
- `DEMO_TS3006c.md`: the cross-meeting demo built from the real S2 output.

One run per system, small sample, non-deterministic model: see `docs/METHOD_EN.md` section 4.1 and `docs/B0_REPORT.md` before quoting any number.
