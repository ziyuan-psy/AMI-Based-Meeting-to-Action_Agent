# SC gold annotation change log: ES2004 and TS3007

Schema version: 0.5.0 (unchanged). Annotation date: 11 October 2026.

## Deliverable

The eight meeting JSON files are the gold-output annotations. This file is an accompanying change log, not a substitute for the gold output. All annotation text is in English. Every action has a reviewed owner/deadline basis; every scored item has a boolean prior-context flag and checked source locators. No unreviewed basis values or empty references remain. The original corpus, frozen schema and other meeting series were left unchanged. Human cross-check remains the release check described in the annotation guide.

## Annotation decisions

- Each item is evaluated at the end of its own meeting. Later meetings do not overwrite earlier states. Scenario citations use phase-appropriate material; prototype instructions released after meeting c are not used to infer a c-meeting deadline.
- Split compound assignments when different owners are responsible for different tasks. TS3007c s.15.1 belongs jointly to ID MTD026UID and UI MTD027ID; s.15.2 belongs to ME MTD028ME. Participant-ID suffixes are not used to guess roles.
- Split compound decisions/problems where their component statuses differ. Speech recognition in ES2004c is conditional; adopted display, buttons, covers and power choices have separate records. The unavailable charging option is resolved by the alternative; speech-recognition affordability remains open.
- Preserve exploratory preferences as proposed rather than adopted. ES2004b's market preference and shell/Video Plus ideas retain their limited scope. TS3007b's touchscreen proposal is recorded without inferring an unseen drawing; TS3007c records the subsequent adopted touchscreen direction.
- Evaluate the positive speech-recognition selection in ES2004d as rejected at meeting end, and the distinct decision to remove it as adopted. The management recommendation is a separate open action. These opposite statements have their own evidence and relationship.
- Treat a stated work interval or next-meeting start time as scheduling context, not automatically a due date. Explicit relative deadlines retain their source wording. TS3007d's final report is in_progress; it is not marked completed merely because the team helped write it.
- Questionnaire actions are retained as explicit administrative obligations. ES2004d's collective questionnaire action is in_progress because D reports completion, while completion by all members is not established.
- Explicit commitments to create minutes are included. TS3007b minutes are completed based on the PM's direct report of an update in the shared folder, not an independent file inspection. TS3007c/d commitments remain open because final delivery/completion is not confirmed. For TS3007c, "at the end" retains the source wording.
- Process problems are retained when explicitly reported. TS3007c's laptop problem has unknown repair status because no direct repair confirmation establishes its end state. An unknown state here represents an evidence limit, not unfinished annotation.
- Receiving emailed instructions is treated as a dependency rather than a separate deliverable-bearing action. The corresponding TS3007b item is excluded and logged below.
- Late cost-information complaints are merged to avoid duplicate scoring. Component-cost compliance in TS3007d is resolved; the separate concern about added software-development costs remains open.
- ES2004b's logo investigation and ES2004c's speech-cost check are explicit tasks with ambiguous assignees. "We" and an acknowledgement do not establish exclusive or joint responsibility reliably, so owner is null and owner_basis is ambiguous. No unsupported owner is invented.
- Cross-meeting links are remapped to the relevant split subitem. Related-item evidence is included in the same reference list; a link does not by itself make prior_context_needed true.

## Record and ID changes

| Operation | Original ID | Resulting ID(s) / reason |
| --- | --- | --- |
| remove | ES2004a.JacquelinePalmer.s.9 | NA. is a non-substantive placeholder, not an evaluable decision. |
| split | ES2004b.JacquelinePalmer.s.13 | ES2004b.JacquelinePalmer.s.13.1; ES2004b.JacquelinePalmer.s.13.2 |
| split | ES2004b.JacquelinePalmer.s.14 | ES2004b.JacquelinePalmer.s.14.1; ES2004b.JacquelinePalmer.s.14.2; ES2004b.JacquelinePalmer.s.14.3 |
| split | ES2004c.JacquelinePalmer.s.10 | ES2004c.JacquelinePalmer.s.10.1; ES2004c.JacquelinePalmer.s.10.2; ES2004c.JacquelinePalmer.s.10.3; ES2004c.JacquelinePalmer.s.10.4; ES2004c.JacquelinePalmer.s.10.5 |
| split | ES2004c.JacquelinePalmer.s.11 | ES2004c.JacquelinePalmer.s.11.1; ES2004c.JacquelinePalmer.s.11.2 |
| merge | ES2004c.JacquelinePalmer.s.12 | ES2004c.JacquelinePalmer.s.11.1; ES2004c.JacquelinePalmer.s.10.1 |
| merge | ES2004d.JacquelinePalmer.s.21 | ES2004d.JacquelinePalmer.s.20 |
| remove | TS3007b.elana.s.12 | Receiving or waiting for emailed instructions is a dependency, without a concrete deliverable or an obligation to send the instructions; excluded from scored actions. |
| split | TS3007c.dharshi.s.15 | TS3007c.dharshi.s.15.1; TS3007c.dharshi.s.15.2 |
| split | TS3007c.dharshi.s.19 | TS3007c.dharshi.s.19.1; TS3007c.dharshi.s.19.2 |
| add | (new annotation) | ES2004b.JacquelinePalmer.s.16 |
| add | (new annotation) | ES2004c.JacquelinePalmer.s.13 |
| add | (new annotation) | TS3007b.elana.s.16 |
| add | (new annotation) | TS3007b.elana.s.17 |
| add | (new annotation) | TS3007c.dharshi.s.23 |
| add | (new annotation) | TS3007d.dharshi.s.20 |
| add | (new annotation) | TS3007d.dharshi.s.21 |

Split IDs use the frozen schema's permitted numeric suffix, such as .15.1 and .15.2. The retired parent and its children are not scored together. Ordinary revisions preserve the original ID. Newly added records use unused integers under the existing meeting/annotation-author prefix; this component is an identifier convention, not the action owner.

## Validation

Checked JSON parsing, every constraint keyword used in the supplied schema through a local contract checker, exact category field sets, unique and valid item IDs, participant mappings and owner citations, basis/value consistency, boolean prior flags, citation existence and deduplication, source paths, nonempty support, same-series chronological relationships, absence of future-meeting evidence, and English annotation content. Git diff whitespace checks are run after writing. These checks do not constitute independent human approval or third-party Draft 2020-12 certification.
