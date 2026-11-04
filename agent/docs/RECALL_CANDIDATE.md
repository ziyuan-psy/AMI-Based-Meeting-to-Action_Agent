# Recall-oriented next candidate

`recall_candidate_v1` implements the user's selected tradeoff: improve overall F1
and reduce missed Issues while accepting more unmatched predictions. It is a
named candidate, not a frozen final release. `development` remains available and
is the compatibility default; select the candidate explicitly for its runs.

From the repository root, using the installed project Python environment:

```powershell
$env:PYTHONPATH = "agent/src"
python -m mta.run TS3006 --mode s2 --quality-profile recall_candidate_v1 --out agent/runs/recall_candidate_v1
```

The series command runs a→d and stops on failure. Use a new output directory for
the first candidate trial. `MTA_QUALITY_PROFILE=recall_candidate_v1` also selects
the profile for existing API/series entry points. Use `development` to run the
previous experimental selector/full-P2 workflow. The held-out-series guard is
unchanged; no held-out data is used to develop this candidate.

## Effective behavior

| Component | Candidate behavior |
|---|---|
| TXT parser, model, reasoning efforts and resource limits | Current implementations/settings retained |
| Extraction and normal consolidation prompts | Exact 414a31f strings in `baseline_prompts.py` |
| Samples and vote | Existing 2 Action samples, 3 capped consolidation samples and evidence-overlap majority vote |
| Action recovery | Current type-aware restoration |
| Issue recovery | Original source hypotheses, excluding exact text/evidence duplicates |
| Caps | Stable 414 order; unchanged 5 Actions / 7 Decisions / 4 Issues |
| New selector and Decision second pass | Not used in this profile |
| Follow-up | Agent chooses lookups/continuation/finish for flagged items; restored hypotheses are also flagged |
| Full P2 hard gate | Disabled for this profile; implementation remains in `development` |
| Validation | Schema and source/scope checks retained; candidate follow-up rejects unread dialogue citations |

Runtime metadata records both the requested config flags and the actual
`quality_policy`, including active prompt hashes. For example, requested
`P2_ENABLED=true` does not conceal that `quality_policy.p2_enabled=false` for
this candidate. Candidate caches use a separate subdirectory. Completed-run
checks include the effective policy, and S2 refuses incompatible earlier outputs.

Issue recovery explicitly ignores merged `candidate_indexes` when deciding which
original hypotheses to restore. The tested 414 cached outputs had no such field.
Lineage is still retained in trace; an index saying a source was merged does not
prove its original concern survived. This reproduces the selected replay instead
of silently suppressing restored Issues in live mergers that now record lineage.

## Assurance and tradeoff

Candidate reports are labeled **Draft candidate**. Targeted follow-up does not
certify every statement, conflict, historical relation or scenario citation.
The P2 sidecar is explicitly disabled with no accepted-item claims; an empty
P2 clarification index does not mean the draft contains no uncertainty. The
human-confirmation section still lists unresolved Action fields. Full P2 checks,
document evidence validation and structured P2 clarifications remain available
in the `development` profile.

Recovered tasks can have incomplete initial citations. Their follow-up flag asks
the agent to inspect supporting dialogue; only delivered dialogue may be cited
in a candidate `finish`. This still needs online validation, particularly the
TS3006a Marketing task whose old candidate omitted the assignment line.

The 16-meeting fixed-response replay gives:

| View | Action F1 | Decision F1 | Issue F1 | Pooled F1 |
|---|---:|---:|---:|---:|
| 414a31f | 0.474 | 0.441 | 0.429 | 0.449 |
| Candidate postprocessing on recorded responses | 0.505 | 0.441 | 0.457 | 0.464 |

Issue P/R changes from 0.455/0.405 to 0.382/0.568: 22 more scored predictions,
6 more matches and 16 more unmatched predictions. These are model-judged draft
gold comparisons, not independent human final evaluation. They are not fresh
online end-to-end results; flagged field checks and cross-meeting outcomes in
the integrated profile still require a real full trial.
