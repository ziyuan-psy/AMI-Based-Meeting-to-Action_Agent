# Cross-meeting linking (development step 6)

S2 retains the Agent's choice of historical query, record, evidence, relation, further search, and pending stop.
The controller enforces evidence provenance, time boundaries and budgets. It never assigns a relation from a
similarity score. S1 exposes no historical tools.

## Read boundary and retrieval

- A meeting can read only itself and earlier meetings of its own series. Historical record targets must be
  strictly earlier; an old record cannot introduce a current, future, or other-series dialogue reference.
- History is an immutable per-run snapshot of actual saved system records. Metadata includes the source
  filename and SHA-256, original text, status, owner/deadline, and reference IDs. Old history is never rewritten.
- `get_prior_items(query, item_type)` searches content keywords. The type filter runs before ranking.
  Spoken acronyms (`T_V_`, `TV`, `television`) are normalized for retrieval and triage only.
- `read_prior_item(item_id, query?, offset?, limit?, window?)` returns the old record and actual cited dialogue
  with neighboring context. The optional query reorders that record's references; paging permits further reads.
  It does not search gold, use future records, or treat reference locators as already-read dialogue.
- Results are bounded as complete JSON rows. Only dialogue actually delivered to the Agent enters its observed
  evidence registry. Omitted lines cannot be quoted as proof.
- Initial item context includes cited lines and one neighboring line on each side, under the same 6,000-character
  bound. The P2 outcome saves `initial_context`, so acceptance/qualification in nearby dialogue is reproducible.

## Check and action loop

After each lookup the Agent reassesses the item. An assessment proposing a relation must identify the old
record, relation, changed fields, concise reason, and both old/current evidence IDs in `checks.relation.links`.
All checks accept an ID list as their preferred evidence input. Non-actions omit owner/deadline checks;
code fills them as not applicable. Field identity for non-conflict findings is derived from the check name.
Code binds those explicitly selected IDs to actual delivered text if the Agent omitted a redundant quote.
It never reads an unobserved ID or replaces an invented quotation with a valid one. The final finding retains
exact quotes in `checks.relation.quotes`; trace `submitted_assessment` and `evidence_bindings` distinguish the
model's selections from deterministic text binding. A no-conflict pass with an empty quotation can anchor to
the valid current statement quotes as its reviewed scope; fail/uncertain conflicts are never changed by this.

`continues` carries forward the same commitment without a changed field. `updates` refines the same subject
(scope, owner, deadline, status); `replaces` supersedes a choice. `cancels` requires explicit withdrawal;
`completes` requires an earlier Action and actual completion; `resolves` requires an earlier Problem;
`reopens` requires evidence that a settled matter became active again. Topic overlap and absence are insufficient.

The model chooses concrete queries and evidence. `retrieve` verifies returned candidates; once candidates
exist and a relation remains uncertain, another item search is exposed through `search_again`, rather than
repeatedly retrieving the same metadata. Dialogue search, record/evidence reads, clarification and stopping
remain available. No particular record ID or relation is forced.

When the statement and conflict checks pass and a historical relation remains unresolved, the controller
prioritizes historical search/reads plus pending stops. Other unknown fields remain unresolved. After cited
historical dialogue is delivered, the controller requests `assess_relation` to focus on the old/new comparison
instead of repeatedly submitting the local checks. This tool can
propose a relation, explain that records are unrelated, request further search or remain pending. Its result is
merged with the earlier checks and passes the same per-link provenance gates; no relation is inferred by code.
A verified relation may coexist with a pending status/owner/deadline; the witness's after fields reflect the
published unknown values rather than the extractor's guesses.

Historical comparisons flagged `POSSIBLE_PRIOR`/`STATUS_CHECK` use `EFFORT_LINK=medium` on the same model
(`MTA_EFFORT_LINK` overrides it); other local checks retain `EFFORT_AGENT=low`. This is recorded in runtime
configuration. It follows observed development failures in which low-effort runs retrieved correct evidence
but skipped the historical comparison. It is not evidence of a general accuracy gain.

The code checks every proposed relation independently against the selected record's reference IDs and the
current meeting's support. It rejects cross-record quote substitution and wrong time scopes. Quote corrections
return the actual line to the model without accepting paraphrases as quotations. The trace retains submitted
checks, validated checks, controller actions, exact tool returns, duplicate/no-progress behavior and termination.

Insufficient evidence clears the proposed link from the canonical record and creates a `related_items`
clarification. A supported current statement can remain recordable while its historical relation is pending.
Missing historical JSON is explicitly pending, even if earlier dialogue exists; it is not a confirmed no-match.

The shared limits remain 4 lookups/item, 30 lookups/meeting, 12 Agent turns/item, 80 model attempts/meeting,
1,000,000 tokens/meeting, 300 seconds/request and 1,200 seconds/meeting by default. Retries consume the same
budget. Existing checkpoint/partial-output preservation applies to these new actions as well.

## Outputs and review

Canonical records keep the existing `related_items: [{id, relation}]` contract. The P2 sidecar adds
`relation_evidence`: the exact retrieved **before** snapshot, **after** item fields, changed fields, reason, and
old/current quotes. `supported` means the automated provenance and semantic checks passed; it is **not a claim
of human validation**. Output validation checks witnesses against the saved historical snapshot and assessed
quotes. Older S2 saved runs lacking the current `LINK_POLICY_VERSION` cannot silently count as current runs.

Reproduce a focused live development case from the `agent` directory:

```powershell
$env:PYTHONPATH = 'src'
python scripts/link_demo.py results/cross_meeting/<new-folder>
# Controlled missing-record ablation using the same real current item:
python scripts/link_demo.py results/cross_meeting/<another-new-folder> --without-records
```

This uses the genuine saved MVP `TS3006b.sys.s.11` as the current item and the complete saved TS3006a baseline
as history. It supplies no expected old record ID, relation, or query to the Agent. It exercises the production
P2 Agent, tools, budget, record assembly and validators; it does not rerun extraction, summary generation or
the complete S2 series. No gold or quality judge is used. It is a development demonstration, not a final
evaluation or an estimate of cross-meeting accuracy.

Every attempt is preserved with input hashes, exact code/schema snapshots, model, configuration, trace,
usage, checkpoint and validated outputs. `run.json` records whether the source history stayed unchanged.
`review.json` records `human_review.status=pending` at generation. An actual person's later review of the
old/new evidence and relation is stored separately. A Codex source audit alone must never be relabeled as human approval.

The completed case and human attestation are in [CASE.md](../results/cross_meeting/CASE.md).
Captured run files retain their at-generation pending review flag. The separate `human_review.json` and
`human_review_links.json` record the user's actual confirmation and match it to the reviewed live evidence;
the original run captures are not rewritten after review.
