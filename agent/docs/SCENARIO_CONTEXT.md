# Scenario passage retrieval and document evidence (development step 7)

The Agent can search AMI scenario passages, read more page context, explicitly assess relevant passages,
and retain approved document citations alongside meeting evidence. It chooses queries, reading ranges,
additional searches and pending stops. No additional model/tool/token/time budget is introduced.

## Retrieval and bounded reading

`ScenarioDocument` snapshots the bytes of `data/scenario_text.txt` once per Store. It splits the existing
physical PDF-page markers, normalizes newlines, and strips the outer whitespace of each page. All character
ranges refer to this canonical page text: zero-based start, end-exclusive. Source SHA-256 refers to the actual
prepared text bytes, not a freshly extracted PDF or an image rendering.

`search_project_doc(query)` uses the existing BM25 retriever over passages of at most 1,000 characters,
with 150-character overlap and preferred line/paragraph boundaries. Search returns the matching passage,
not the first 500 characters of a matching page. Spoken-acronym normalization is shared with meeting retrieval.
Each result identifies `passage_id`, source SHA-256, physical PDF page, start/end, text, page metadata and score.
IDs such as `scenario:p66:1569-1842` are stable for the same source/range; the source hash distinguishes versions.

`read_project_doc(page, offset=0, limit=1800)` reads a page in portions, with a hard maximum of 2,000 characters
per read. `next_offset=null` means the page is complete. Unknown pages and invalid ranges fail explicitly.
Both tools are subject to the existing 6,000-character result bound and shared lookup budget. Only complete
rows actually delivered to the model enter the observed-document registry. Different ranges on the same page
count as new observations; re-reading identical content does not.

## Document selections and checks

Each finding may include `documents`, a list of:

```json
{
  "passage_id": "<an actually delivered passage ID>",
  "result": "context",
  "reason": "Explain what this source clarifies for this check"
}
```

Results are `supports`, `context`, `irrelevant`, `contradicts` or `uncertain`. The model supplies the relevance
judgment. Code verifies delivery, source version, page, canonical range, exact text and metadata. A search hit
that the model never selects is not a citation. `contradicts`/`uncertain` makes the corresponding check uncertain
and drives further lookup/search or a pending clarification. `irrelevant` is reviewed but never cited.

Positive checks still require current-meeting dialogue evidence. Scenario instructions cannot establish who
actually accepted an assignment, an agreed deadline, adoption or a historical change. Historical links retain
the separate requirements to retrieve the old record and read both old and current dialogue evidence.

The default workflow does not require a scenario lookup for every item. An internal caller may set
`verify_project_context=true` and supply a `verification_request` to explicitly ask for a context audit. The
statement remains pending until a valid passage is explicitly assessed, including an explicit irrelevant
assessment where appropriate. The model may still stop pending instead of completing the audit.

After document retrieval, `assess_document_context` focuses on the current statement and selected passages;
optional `status_check` can resolve a remaining status gap. Other unresolved fields are retained. Relevant
tools are offered according to the remaining check, while the Agent selects actual searches and ranges.
Explicit context audits use the existing `EFFORT_LINK=medium` setting; ordinary local checks retain
`EFFORT_AGENT`. The limits remain 80 model attempts, 1,000,000 tokens, 300 seconds/request, 1,200 seconds/meeting,
4 lookups/item, 30 lookups/meeting and 12 model turns/item. Retries and bounded reassessments share those limits.

## Publication and trace

P2 `safe_fields` retains only document selections attached to passed, unblocked findings for a recordable
statement. It merges their references with validated dialogue references. It never copies all `pages_seen`.
The primary record remains schema 0.5.0:

```json
{
  "source_type": "project_document",
  "source": "amiScenario.pdf",
  "locator": "PDF page 66 (one-based), chars 1569-1842 (zero-based, end-exclusive)"
}
```

The P2 sidecar adds optional `observed_documents` and `document_evidence`. Evidence witnesses retain check,
model judgment/reason, publication flag and original delivered text/hash/range. Output validation checks these
against actual source snapshots and recorded tool returns, and rejects unselected or altered document citations.
The generated Markdown report displays retained passages. The application UI and primary field names are unchanged.
Generic checkpoint/partial-output preservation also saves the new registry and witnesses on interruption.

The input protocol accepts missing quote arrays as empty, but positive facts cannot pass without valid current
support. Identical redundant root fields and an empty root `documents` array are logged and discarded; conflicting
duplicates are rejected. A missing current-evidence selection can receive one bounded correction, and a non-action
stop hint based on inapplicable owner/deadline can receive one correction. Both count against the original model
budget/turn limit and never select facts on the model's behalf.

`DOCUMENT_POLICY_VERSION`, passage settings, source code, prompts and model are recorded. Completed P2 runs are
not reused after document-policy, source hash or passage-setting changes. Frozen earlier development captures
are preserved rather than rewritten.

## Verification and scope

See [the development capture](../results/scenario_context/CASE.md) for genuine saved TS3006d input, actual model
queries, selected evidence, retained references, source audit, failed attempts and reproduction commands. The
LCD case isolates the first clause of a compound saved MVP decision; the full original is preserved. Its
explicit project-context request does not demonstrate spontaneous document use. No gold is read by the runtime,
and the capture does not rerun extraction/summary generation or final evaluation.

Source/range validation proves provenance, not semantic entailment. Relevance and conflict judgments still
require model/human assessment. Scenario Time/Role metadata is exposed as context; there is no verified mapping
from every scenario message to AMI meeting-clock eligibility. The retriever searches the prepared scenario
document globally as before. It must not be described as enforcing a new stage/time access policy. Current
meeting evidence and the same-series/earlier-meeting boundaries remain mandatory.
