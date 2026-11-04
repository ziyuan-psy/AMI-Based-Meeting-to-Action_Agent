# Frozen evaluation scores

Status: **COMPLETED**. DEVELOPMENT_VALIDATION

Semantic checks: none. Three-vote alignment; raw vote caches retained.

| Mode | Completed | Action F1 | Decision F1 | Issue F1 | Pooled P / R / F1 | Owner correct / matched |
|---|---:|---:|---:|---:|---|---|
| s2 | 16/16 | 0.505 | 0.441 | 0.457 | 0.422 / 0.517 / 0.464 | 17/25 |

Pooled is micro F1 from summed TP/system/gold, not the average of type F1. See `summary.json` for each type P/R/F1 and denominators.
Prediction costs and judge costs are separate. With shared_s1_s2, S2 prediction costs exclude reused extraction/merge.
B0 at this commit has the current one-shot prompt; it is not the teammate’s 414a31f arm. S1/S2 are the matched-content context comparison.
An empty P2 clarification sidecar is not proof of full evidence verification. Relation accuracy uses matched source items and is reported separately from extraction F1.
