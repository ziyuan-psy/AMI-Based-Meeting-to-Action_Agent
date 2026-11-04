# Recall candidate validation

The selected `recall_candidate_v1` is implemented and explicitly selectable.
It preserves 414 content prompts and stable caps, current type-aware Action
recovery and original Issue hypothesis recovery. The default `development`
profile remains available with full P2. No commit or push was made in this step.

All 16 recorded-response replays ran through the production extractor cache and
candidate consolidator. Per-type texts exactly match the selected earlier views;
all scores replay from actual saved three-vote judgements without API calls:

| View | Action F1 | Decision F1 | Issue F1 | Pooled F1 |
|---|---:|---:|---:|---:|
| 414 reference | 0.474 | 0.441 | 0.429 | 0.449 |
| Candidate recorded-response replay | 0.505 | 0.441 | 0.457 | 0.464 |

The separate a→b smoke is a scripted offline engineering check, not a genuine
model-generated example or evaluation score. It exercises TXT→JSON/report,
budget admission, retrieval→historical evidence read→finish, disabled-P2 sidecars,
and effective-policy recording. Reports identify the candidate as a draft and
do not claim full evidence/conflict verification.

Model/generation/resource settings are unchanged; the selected quality profile
and its effective differences are explicit in study/runtime metadata. Earlier
version-selection artifacts and UI/annotation protection hashes are unchanged.
The held-out set was not opened. Full-suite offline test results are in tests.json.

This is not a new cold-cache online S2 trial. Actual model field/citation quality,
cross-meeting outcomes, and full-run completion still need online validation.
In particular, the restored TS3006a Marketing task needs its assignment-line
citation checked; the new source-review flag requests inspection, not certification.
