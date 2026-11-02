# Development regression, step 8

- [Final comparison and audit](regression-v2/REGRESSION.md): genuine fresh baseline calls retained from v1,
  current production runs after an artifact-write repair, and same-protocol scoring.
- [First-attempt failure](regression-v1/ATTEMPT.md): all three baseline pipelines completed; the first current
  pipeline failed before any model call because a Windows temporary checkpoint path exceeded MAX_PATH.
- [Protocol and reproduction](../../docs/DEVELOPMENT_REGRESSION.md).

The captures are SB development checks, not held-out evaluation, independent human review or a stability study.
Gold is a fixed snapshot of the existing draft annotations. Versions, model/settings, inputs, actual responses,
tool histories, failures and runtime accounting remain available. UI and original annotations are unchanged.
