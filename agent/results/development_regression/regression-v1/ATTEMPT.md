# First attempt: preserved infrastructure failure

The historical `414a31f` policy completed IS1008a, TS3006a and TS3006b with fresh caches and arm-local history.
Its calls, records, normalized inputs, source/configuration and original timings remain in `baseline/`.

The first current-policy run failed at the initial checkpoint, after transcript parsing and before any model
request. See [the result](current/IS1008a.result.json), [metadata](current/s2/IS1008a.run.json) and
[stage trace](current/s2/IS1008a.stages.jsonl). The failure is FileNotFoundError at the parse stage, with zero
model attempts and zero charged tokens. Later current meetings were not run; all quality pairs are unscored,
not counted as empty failed predictions.

Reproduction isolated a Windows path problem: the checkpoint directory was 218 characters, the valid target
`parsed.json` 230, and its destination-name-plus-UUID temporary 268. The repair uses a unique UUID-only sibling
temporary (257 characters here), retaining atomic replacement and cleanup. A regression test covers a deep
Windows directory and a long valid POSIX filename.

[regression-v2](../regression-v2/REGRESSION.md) captures the repair, preserves these completed baseline calls
without resampling them, and runs the current arm and scoring. Baseline reuse requires identical inputs, draft
gold, historical revision, model/configuration and common transport. [Reuse provenance](../regression-v2/baseline_reuse.json)
retains the original source-study hash and copied artifact hashes. No original attempt is overwritten.
