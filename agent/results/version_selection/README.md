# Version selection against 414a31f

This commit records component comparisons and the resulting recommendation. It does
not apply the recommendation to the default pipeline or commit the earlier pending
Issue-development source changes.

See `213ccfc-v1/REPORT.md` and `213ccfc-v1/decisions.json`. The recommended minimal
Action recovery has 16-meeting fixed-response replay scores, not a new complete
online pipeline result. The report identifies missing generations and scores,
precision/recall tradeoffs, and source-review limitations.

Verify the saved evidence without API calls from the repository root:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location).Path 'agent\logs\trace_step_deps')
& '.\.venv-ui\Scripts\python.exe' -X utf8 agent/results/version_selection/verify_saved.py
```

Use the project's installed Python environment. The verifier checks saved-file
hashes and replays all 107 available scored views from recorded votes. It does not
instantiate a model client or require the earlier uncommitted experiment directories.
The original capture/finalization drivers additionally reference those local source
cohorts, as recorded in `study.json` and `preservation.json`.

`COMMIT_SCOPE.json` inventories the files in this commit. Two workspace-preservation
files are intentionally local only: the annotation README snapshot and the full
uncommitted patch containing its changes. They remain on disk and are ignored here;
the frozen experiment manifest retains their historical hashes. The original UI,
annotation documents, earlier experiment directories and pending production changes
are outside this commit.

The frozen report's "not committed" statement describes the experiment capture
time. The default pipeline remains unchanged by this evidence-only commit.
