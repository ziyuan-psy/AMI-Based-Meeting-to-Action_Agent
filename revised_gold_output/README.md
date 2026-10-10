# AMI Revised Gold Annotation Guide

Schema version: 0.5.0  
Preparation date: 10 October 2026  
Status: unreviewed annotation drafts

## Purpose

This folder provides working copies for manual review of the six selected AMI series. Review meetings a–d as one continuous project sequence, while recording each item's meaning and state as of its own meeting.

These files are starting points, not final gold annotations. Original summary statements may contain errors, incomplete evidence, or unresolved ambiguity. Check them against the dialogue and relevant context before accepting them.

## Files

There are 26 files in this folder: this README, 24 meeting JSON files (four for each selected series), and the JSON Schema file `revised_gold schema template.json`.

| Series | Split | Meeting files (a–d) | Original records |
| --- | --- | --- | --- |
| ES2011 | SB development | `ES2011a.json` through `ES2011d.json` | 77 |
| IS1008 | SB development | `IS1008a.json` through `IS1008d.json` | 65 |
| TS3004 | SB development | `TS3004a.json` through `TS3004d.json` | 78 |
| TS3006 | SB development | `TS3006a.json` through `TS3006d.json` | 77 |
| ES2004 | SC held-out evaluation | `ES2004a.json` through `ES2004d.json` | 58 |
| TS3007 | SC held-out evaluation | `TS3007a.json` through `TS3007d.json` | 70 |

Each meeting has its own flat JSON array, copied from the corresponding `ami-corpus/abstractive/<meeting>.json`. Filenames follow the original lowercase meeting suffix, for example `IS1008a.json`, `IS1008b.json`, `IS1008c.json`, and `IS1008d.json`. Original record order within each meeting is preserved. The meeting is identified by the filename and the prefix of each item's `id`; no meeting wrapper or extra meeting field is needed.

All original `id`, `text`, and `type` values are copied unchanged. Only the required revised-schema fields have been added. No original records have been removed, split, merged, or corrected during preparation. All 425 source records are included across the 24 meetings.

Edit these derived copies during annotation. Leave the original corpus files unchanged.

## Fields

| Field | Applies to | Meaning |
| --- | --- | --- |
| `id` | All | Stable item identifier. Preserve the original ID for a one-to-one revision. Its annotation-author component is not the action owner. |
| `text` | All | The statement being annotated. Correct it in this derived copy when evidence justifies a revision; preserve conditions, scope, and uncertainty. |
| `type` | All | One of `abstract`, `actions`, `decisions`, `problems`. |
| `reference` | All | Supporting source locators, each with `source_type`, `source`, and `locator`. Initial entries are original summlink candidates; final gold retains human-checked support. |
| `owner` | Actions | Assigned owners' stable participant IDs, or `null`. Multiple IDs mean joint ownership, not competing guesses. |
| `owner_basis` | Actions | How ownership is established: `explicit`, `inferred`, `ambiguous`, `missing`, or `unreviewed`. |
| `deadline` | Actions | Original deadline wording, or `null`. |
| `deadline_basis` | Actions | How the deadline is established, using the same five basis values. |
| `status` | Actions, decisions, problems | State supported at the end of this item's meeting. |
| `prior_context_needed` | Actions, decisions, problems | Whether earlier-meeting context is necessary to interpret or verify the item. |
| `related_items` | Actions, decisions, problems | Earlier action/decision/problem records affected by this item, with a relationship label. |

Abstract records have 4 fields; decisions and problems have 7; actions have 11. All fields listed for a category are required. Do not add extra optional fields.

### Initial values

The prepared files contain imported reference candidates and unreviewed placeholders for the other added labels:

- All records: `reference` is prefilled from the matching original summlink entry where links exist. An empty list means no original link was imported.
- Actions: `owner: null`, `owner_basis: "unreviewed"`, `deadline: null`, `deadline_basis: "unreviewed"`.
- Actions, decisions, problems: `status: "unknown"`, `prior_context_needed: null`, `related_items: []`.

Imported references are unverified candidates, not accepted evidence. The remaining null/empty values and `unreviewed`/`unknown` labels are unfinished annotations. They do not establish that an owner, deadline, supporting source, or earlier relationship is absent.

## References and context

Each reference identifies a source and a specific location. Prefilled entries require human support checking before they can be accepted as final gold:

```json
{
  "source_type": "dialogue",
  "source": "ami-corpus/dialogueActs/IS1008c.json",
  "locator": "IS1008c.A.dialog-act.s9553330.122"
}
```

The example illustrates the format used for the imported links. Preparation matches each revised record's `id` to `summlink/<meeting>.json` → `abstractive.id`, then imports the linked `extractive[].id` values as dialogue references. The `source` points to the corresponding primary `dialogueActs` file. Duplicate links within one item are removed while preserving their original order.

Across the 24 files, 4,105 item-to-dialogue links were imported for 417 of the 425 records. The other 8 records retain `reference: []` because no original linked dialogue was available. This is an annotation gap to review, not proof that supporting evidence does not exist. Locator existence was checked mechanically; evidential support was not checked by this import.

| `source_type` | Use for |
| --- | --- |
| `dialogue` | Transcript content from the current meeting or earlier meetings in the same series. |
| `participant_metadata` | Speaker, participant, role, or addressee attributes; original `meetings.xml` entries when needed as a fallback. |
| `project_document` | Relevant AMI scenario instructions or other project documents. |

`source` is a path relative to the data workspace root containing `ami-corpus` and the project documents, not this output folder. The original corpus and project documents are maintained separately from this annotation folder. `locator` identifies the dialogue-act ID, metadata record/attributes, or verified document page and section. For example, a scenario citation uses `source: "amiScenario.pdf"` and the specific checked page/section as its locator.

During human review, check the prefilled links against the actual dialogue. Remove irrelevant or incorrect links and add missing support. For final gold, keep sources actually checked and used. A raw summlink may support only part of a compound statement, and a link supporting the task does not automatically support its owner or deadline. The final reference list must collectively support the statement and each resolved owner, deadline, status, or change. Verify each label separately even though the citations share one list.

Use participant metadata from the actual meeting. Speaker letters can change between meetings. Consult original `meetings.xml` if JSON metadata is absent or conflicting. Scenario responsibilities help interpretation but cannot override actual meeting assignments.

### Prior context versus related items

- `reference` answers: **What sources support this annotation?**
- `prior_context_needed` answers: **Is an earlier meeting necessary to interpret or verify it?**
- `related_items` answers: **Which earlier project item does this continue or change, and how?**

Participant or scenario lookup alone does not make `prior_context_needed` true. An earlier-meeting citation can provide corroboration without being necessary. Conversely, earlier context can be needed even when there is no earlier action/decision/problem record to link.

Use this shape for an item relationship:

```json
{
  "id": "<earlier item ID>",
  "relation": "updates"
}
```

Replace the placeholder with a real item ID. Allowed relationships are `continues`, `updates`, `replaces`, `cancels`, `completes`, `resolves`, and `reopens`. Use `[]` when no relationship exists after review. Link only to earlier action/decision/problem items in the same series, including earlier items in the same meeting when appropriate. Cite supporting evidence in `reference`.

Use only information available by the current meeting's end, including scenario material applicable at that stage. Do not use later meetings to fill earlier ownership, deadlines, or state. Record later updates in later linked items.

## Owners, deadlines, and status

The basis values mean:

| Basis | Meaning |
| --- | --- |
| `explicit` | Directly stated or unambiguously addressed; participant metadata may resolve the person's identity. |
| `inferred` | Supported by a justified contextual inference. |
| `ambiguous` | Relevant cues exist but do not support one reliable interpretation. |
| `missing` | Human review found no adequate cue. |
| `unreviewed` | The annotation is unfinished. |

For explicit or inferred ownership, enter the assigned participant IDs. For ambiguous, missing, or unreviewed ownership, use `null`. The person announcing an assignment is not automatically its owner. Do not expand an unspecified “we” to all participants without support.

Keep deadlines in their original wording. “By next meeting” can have an explicit basis even though it is relative. Do not invent calendar dates or turn a work window, testing duration, expected email, or start condition into a due date. Explicit or inferred deadlines need a non-null value. Missing and unreviewed deadlines must be null. For an ambiguous deadline, retain the relevant phrase if useful, or use null.

Allowed status values:

| Type | Status values |
| --- | --- |
| `actions` | `open`, `in_progress`, `completed`, `cancelled`, `deferred`, `unknown` |
| `decisions` | `proposed`, `adopted`, `conditional`, `rejected`, `deferred`, `unknown` |
| `problems` | `open`, `resolved`, `deferred`, `unknown` |

Distinguish suggestions and conditional offers from accepted commitments. Do not infer full task completion solely from a later presentation. A later cancellation or completion must not overwrite an earlier record's historical state.

## Review workflow

1. Read each series in a–d order. Use `abstractive` as the starting list and `summlink` plus `dialogueActs` to check the actual evidence. Use `topics` when helpful.
2. Check the statement, category, owner, deadline, and state. Consult earlier meetings, participant metadata, or applicable scenario/project material when needed.
3. Verify the prefilled reference candidates and fill the remaining required labels. Add missing participant, prior-meeting, or project-document references when needed. Preserve genuine ambiguity instead of guessing.
4. Record disagreements, unsupported claims, exclusions, ID changes, and questions in the team's review notes. Preserve original IDs for ordinary revisions; document splits, merges, additions, and removals.
5. Complete human cross-check and coverage review before releasing final gold.

Finalization requires no `unreviewed` basis values, boolean prior-context flags, checked references and participant mappings, and valid chronological item links. Genuine ambiguity or an `unknown` state may remain if the team agrees the evidence does not resolve it. Resolve or document evidence gaps and scoring exclusions; never invent a source locator. Passing JSON-schema validation alone does not establish correctness or completeness.

The machine-readable contract is [revised_gold schema template.json](<revised_gold schema template.json>) in this folder. It defines validation rules and is not a meeting annotation file.

## Held-out evaluation boundary

ES2004 and TS3007 remain held out. Their files were prepared by mechanical copying, importing original summlink locators, and adding placeholders. Their content was not qualitatively reviewed for this preparation.

Use SB to settle the schema, annotation guideline, and system design before SC annotation. SC label preparation and human verification must follow the frozen protocol. Do not use SC meetings, annotations, or review findings for prompt tuning, retrieval tuning, agent-instruction changes, schema or validation-rule changes, debugging, or demo selection.

Keep all expected gold outputs outside runtime agent inputs. Human annotators may use these draft copies; runtime agents receive permitted source material and produce their own outputs and references.
