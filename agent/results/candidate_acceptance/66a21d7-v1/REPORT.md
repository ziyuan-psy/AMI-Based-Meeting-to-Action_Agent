# Real development acceptance: 66a21d7 / recall_candidate_v1

**Verdict: PASSED WITH DOCUMENTED LIMITATIONS.** All four cold, real API S2 meetings completed once in a→d order. The runtime is ready to hand to the teammate for the fixed final evaluation; this is not a claim of final accuracy.

| Meeting | Completion | Seconds | Model attempts | Charged tokens | A / D / I | Links |
|---|---|---:|---:|---:|---|---:|
| TS3006a | COMPLETED | 181.7 | 32 | 190,119 | 5 / 3 / 4 | 0 |
| TS3006b | COMPLETED | 268.6 | 34 | 288,713 | 3 / 5 / 4 | 2 |
| TS3006c | COMPLETED | 266.5 | 41 | 301,959 | 5 / 5 / 4 | 0 |
| TS3006d | COMPLETED | 280.0 | 47 | 370,210 | 3 / 6 / 4 | 10 |

Total: 154 model attempts, 1,151,001 charged tokens, 996.9 summed meeting seconds. Counts include pipeline format retries and follow-up turns; judge calls: 0. Per-meeting budgets were unchanged and respected.

## What passed

- Actual TXT→parser→model extraction→merge/vote/recovery/cap→follow-up→summary→validated JSON/report publication.
- Code/schema/input hashes, model, active prompts, config and effective policy stayed fixed. Fresh caches; no response replay or seeded prior outputs.
- All JSON and JSONL parse, output schemas, report/sidecar identities and hashes, completion markers, checkpoints, request accounting, request/meeting timeouts and lookup/concurrency limits.
- Final dialogue references exist and stay in the current/same-series earlier scope. Successful Agent finishes cite only dialogue actually delivered to that item.
- Independently parsed every native run JSON/JSONL: 107 JSON files and 20 JSONL files; see `format_audit.json`.
- The two targeted TS3006a UI/Marketing tasks contain the real PM assignment and recipient acknowledgement, correct explicit owner, and no invented deadline. See `source_review.json`.

## Recovery events and limits

- 1 unusable consolidation response attempt(s) were retried by the pre-existing bounded protocol. 4 recoverable Agent error event(s) remain visible in `audit.json` and the original traces.
- TS3006b passed a system-record ID to `read_dialogue`; this was rejected. Attempts to cite unread historical dialogue were also rejected before a finish using observed current support.
- 12 cross-meeting link(s) were generated. `cross_meeting_cases.json` preserves old record → actual retrieval/tool returns → Agent finish → new record. Source/scope validity is checked; semantic correctness of every relation is not certified.
- Full P2 is intentionally disabled for this recall candidate. Reports remain Draft candidate. Empty P2/clarification sidecars are an explicit disabled state, not a statement that all evidence/conflicts/fields passed.
- Higher Issue recall admits unmatched/low-relevance hypotheses. Task/Decision duplication, granularity, qualifiers and historical relations remain evaluation concerns; no tuning or filtering was performed during acceptance.
- No new F1 was computed here. The .464 development pooled F1 belongs to the earlier 16-meeting fixed-response replay, not this online run or the held-out set.

## Handoff and preservation

- Final evaluation stays with the teammate. Neither held-out series was opened or run. See `../HANDOFF.md` for the fixed entry point, cache policy, metrics and evaluation-adapter work.
- 28 UI/annotation files and 1061 prior frozen artifacts were hash-checked unchanged. Existing untracked experiment directories were not overwritten.
- No production source/config, UI or annotation document was changed. Only this acceptance package was added. No commit or push.
- The transfer manifest covers this entire package; `python ../verify_package.py` performs an offline integrity check.
