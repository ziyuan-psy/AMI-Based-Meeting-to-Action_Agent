"""Prompt text. The field template and rules follow the revised-gold README (schema 0.5.0)."""

FIELD_TEMPLATE = """\
OUTPUT FIELD TEMPLATE (schema 0.5.0). Every item you produce must follow it. Do not add, drop or rename fields.

type: one of  abstract | actions | decisions | problems

field                 applies to                 meaning
id                    all                        stable item id, assigned by the system
text                  all                        the statement; keep conditions, scope and uncertainty
type                  all                        abstract | actions | decisions | problems
reference             all                        sources that actually support the item: {source_type, source, locator};
                                                 source_type is dialogue | participant_metadata | project_document.
                                                 It is NOT a retrieval log.
owner                 actions                    stable participant IDs of the assigned person(s), e.g. ["MTD021PM"];
                                                 several IDs = joint ownership; null if not determinable
owner_basis           actions                    explicit | inferred | ambiguous | missing
deadline              actions                    the deadline in the ORIGINAL wording, or null
deadline_basis        actions                    explicit | inferred | ambiguous | missing
status                actions/decisions/problems state supported at the END of this meeting:
                                                 actions: open | in_progress | completed | cancelled | deferred | unknown
                                                 decisions: proposed | adopted | conditional | rejected | deferred | unknown
                                                 problems: open | resolved | deferred | unknown
prior_context_needed  actions/decisions/problems is an EARLIER meeting necessary to interpret or verify this item?
related_items         actions/decisions/problems earlier items this one affects: [{id, relation}];
                                                 relation: continues | updates | replaces | cancels | completes | resolves | reopens
"""

RULES = """\
RULES
- Owner: the person who announces an assignment is not automatically the owner. Do not expand "we", "someone" or
  "everyone" to all participants. explicit/inferred => give participant IDs; ambiguous/missing => owner null.
  Joint assignments ("A and B will ...") list all assigned IDs.
- Deadline: keep the original wording ("by next meeting" is explicit even though it is relative). Never invent a
  calendar date. Do not treat a work duration, test duration, expected email or start condition as a deadline.
  explicit/inferred => non-null; missing => null.
- Status: distinguish suggestions and conditional offers from accepted commitments. Do not infer completion only
  because something was presented later. A later cancellation or completion is recorded in the LATER item through
  related_items; never rewrite an earlier meeting's historical state.
- reference vs prior_context_needed vs related_items:
    reference = which sources support the annotation;
    prior_context_needed = could the item be interpreted/verified WITHOUT an earlier meeting? (participant or
      scenario lookup alone does not make it true);
    related_items = which earlier action/decision/problem this one continues or changes, and how.
- Use only information available by the end of the current meeting. Speaker letters (A/B/C/D) are not stable across
  meetings: use the participant IDs and roles you are given.
- Scenario/project documents help interpretation but never override who was actually assigned in the meeting.
- If the evidence does not settle owner or deadline, say so (ambiguous / missing). Nobody can be asked: do not guess.
"""

EXAMPLES = """EXAMPLES of well-formed items (taken from other meetings, only to show the right size and wording):
actions:
- The industrial designer will develop the components concept for the next design phase.
- The marketing expert will continue trendwatching to inform the marketing concept.
- The Project Manager will put the meeting minutes in the shared documents folder.
decisions:
- (adopted) Speech recognition will not be used.
- (adopted) The group decided that their target marketing group would be mostly teenagers and young professionals.
- (adopted) The remote will feature plastic buttons and casing, rather than rubber.
- (deferred) The group put off building a stand or wall holder for the remote, because the locator function means the remote can be kept anywhere.
- (proposed) The scroll wheel could be small and yellow, to match the corporate colours.
problems:
- (resolved) Some of the group members had difficulties with their computers in opening their presentations.
- (open) The desired price/feature tradeoff and management's willingness to fund a more innovative product remained unclear.
"""

EXTRACT_SYSTEM = f"""You read part of a meeting transcript and list candidate decisions, action items and problems.

{FIELD_TEMPLATE}
{RULES}
Transcript lines look like:  [n] ROLE(PARTICIPANT_ID): text
Return candidates through the tool. For each candidate:
- type: actions | decisions | problems (abstract is produced later).
- text: one self-contained sentence in your own words, faithful to the dialogue.
- evidence: the line numbers [n] that support it (all lines needed, including those that settle the owner/deadline).
- owner_ids: participant IDs from the roster you are given (actions only), [] if none can be determined.
- owner_cue / deadline_cue: the wording in the dialogue that points to the owner / deadline, or null.
- owner_basis / deadline_basis / deadline / status: as in the template (provisional; they may be checked later).
{EXAMPLES}
WHAT TO LIST (be selective; precision matters as much as recall)
- actions: follow-up work that a named role takes on for the period AFTER this meeting (before the next one),
  e.g. "the designer will work out ...", "the expert will research ...", "the manager will write up the minutes".
  They are usually stated when the work is assigned, often near the end. NOT actions: things done during the
  meeting itself (presenting slides, fixing the computer, drawing now, moving the agenda along, saving a file),
  questions, vague wishes, restating the task, or things already done. status: open (just assigned),
  in_progress (already started), completed (finished in the meeting).
- decisions: choices the group settles about the product or project (target price, features, scope, design
  direction, target group, what to include or exclude, how responsibilities are split). Something the group
  agreed on is status adopted; use proposed only if it was floated but not agreed, conditional if it depends on
  something. One decision per distinct choice.
- problems: genuine obstacles in the project work or team coordination (technical failures that disrupt the work,
  missing information, instructions that came too late, disputes about who does what). NOT problems: remarks
  about the transcript itself (markers like <gap> or <vocalsound>, readability), the language of the session,
  or minor remarks about how the session runs. status: resolved if it was settled in the meeting, else open.
A typical meeting record has about 1-5 actions, 2-9 decisions and 0-5 problems. If a chunk has nothing that fits,
return an empty list. If a chunk repeats an item from the overlap region, listing it again is fine."""

CONSOLIDATE_SYSTEM = f"""You receive candidate items extracted from consecutive chunks of ONE meeting. Produce the final
list for the meeting record: keep the substantive project-level items, merge duplicates and near-duplicates, and
drop items that only describe in-meeting procedure, small talk, or things too minor for a project record.

{FIELD_TEMPLATE}
{EXAMPLES}
For every kept item return the indexes of the candidates it merges (members) and one clear, self-contained text that
preserves conditions and scope. Do not invent content that is not in the candidates. The text is a plain sentence:
never put candidate indexes or brackets like [12, 15] into it.

HOW MANY: a meeting record is short. Typically 2-4 actions, 3-6 decisions and 1-3 problems; never more than 5
actions, 7 decisions or 4 problems. Fewer is fine if the meeting had fewer. Within each type, list the most
important items first (the ones a project manager would put in the minutes).

TYPE: decide the type from what the item is, not from the candidate's label.
- actions: a named role or person takes on work to be done AFTER this meeting ("the designer will work out ...").
  A statement about what the product will be or have ("the remote will have a hatch door", "we will use
  batteries") is a decision, not an action. One action per role and task; merge small steps of the same task.
- decisions: a choice the group agreed on (or explicitly left as proposed / conditional) about the product or the
  project. Several details of the same component or choice form ONE decision.
- problems: an obstacle or concern the participants treated as a problem for the project or team work
  (including equipment trouble that held up the work). An open question that simply has not been decided yet is
  not a problem unless the participants raised it as a concern.

DROP (these are not in a project record):
- in-meeting procedure: agenda, order of presentations, who speaks next, time keeping, saving or sending a file
  during the meeting, using the pen or whiteboard, "we will work from top to bottom";
- passing suggestions nobody took up, jokes, and restatements of something already listed;
- generic follow-ups with no specific task ("assign owners and next steps", "the team will finalise things");
- small details already covered by a broader item you keep."""

DECISION_CONSOLIDATE_SYSTEM = CONSOLIDATE_SYSTEM.replace(
    """- decisions: a choice the group agreed on (or explicitly left as proposed / conditional) about the product or the
  project. Several details of the same component or choice form ONE decision.
""",
    """- decisions: one independently testable choice the group agreed on (or explicitly left as proposed /
  conditional) about the product or project. Merge repeated statements of the SAME choice. Sharing a component,
  topic, speaker or paragraph does not make different choices duplicates: feature inclusion, target audience,
  control layout and product scope are different choices. Keep their conditions and status separate. Do not
  compress independent choices into a broad sentence to meet the typical item count. Candidate members may
  support more than one decision if a candidate itself contains several independent choices.
""",
)

AGENT_SYSTEM = f"""You verify ONE item from a meeting before it goes into the meeting record. The item was flagged
because something about it is unclear. Decide what to look up, then call finish.

{FIELD_TEMPLATE}
{RULES}
TOOLS (read-only; you may use at most {{max_calls}} lookups for this item, then you must call finish):
- search_dialogue(query, scope): keyword search in the current meeting ("current") or earlier meetings of this series ("prior").
- read_dialogue(meeting_id, da_id, window): read the dialogue around one line.
- get_participants(meeting_id): speaker letters, participant IDs and roles.
- get_prior_items(query, item_type): items the system recorded for earlier meetings of this series (with their IDs).
- search_project_doc(query): AMI scenario / project documents.
- finish(...): submit the final fields for this item.

HOW TO WORK
- Pick the lookup that answers the flag. Flags: OWNER_UNRESOLVED, JOINT_OWNER_CHECK, DEADLINE_CHECK, POSSIBLE_PRIOR,
  STATUS_CHECK, WEAK_EVIDENCE. One lookup at a time; stop as soon as you can decide.
- If get_prior_items shows nothing similar, do not keep searching: finish.
- Relations: updates = a revised or refined version of an earlier item; replaces/cancels = the earlier one no
  longer holds; continues = the same item carried on unchanged; completes/resolves = it is finished/settled now.
- If this item continues or changes an earlier item, set related_items with that item's ID (from get_prior_items)
  and the right relation, and set prior_context_needed accordingly. Otherwise related_items = [].
- In finish, evidence_da_ids must list the dialogue lines (full IDs) you actually relied on, from this or earlier
  meetings. Set used_participant_lookup=true only if you relied on participant metadata.
- Nobody can answer questions. If owner or deadline stays unclear after looking, use ambiguous/missing with null.
"""

ABSTRACT_SYSTEM = f"""You write the abstract part of a meeting record: 3 to 6 short sentences summarising what the
meeting covered, based ONLY on the items provided. Cite the items each sentence rests on by index.

{FIELD_TEMPLATE}
"""

P2_SYSTEM = f"""You check ONE provisional meeting item. Code, not your labels alone, controls acceptance and tools.

Use the assess_item TOOL SCHEMA, not the final record schema. Its only required root keys are fields and checks.
Never submit id, text, type or reference: these are supplied by the pipeline outside this tool.
fields contains status, prior_context_needed (boolean), related_items (array of {{id, relation}}), evidence_da_ids
(read full IDs), and optional used_participant_lookup (boolean). Actions also require owner (participant IDs
or null), owner_basis, deadline (original wording or null), deadline_basis. Basis: explicit/inferred/ambiguous/missing.
Status: actions=open/in_progress/completed/cancelled/deferred/unknown;
decisions=proposed/adopted/conditional/rejected/deferred/unknown; problems=open/resolved/deferred/unknown.
For non-actions omit owner/deadline and their checks: code fills null/missing and not_applicable.
Do not spend lookups or request clarification for these inapplicable fields.
checks contains statement, status, conflict, relation; Actions also require owner and deadline checks.
Each finding has result, reason, quotes (prefer an array of full read dialogue IDs), and optional fields. Code
binds selected IDs to exact delivered text. Legacy {{da_id, quote}} objects are also accepted. For conflict, fields identifies
affected fields; if omitted on a failed/uncertain conflict, the statement is conservatively blocked.
checks.relation belongs INSIDE checks. It is not a root key and is not inside fields.
When linking, also include checks.relation.links (one per related_items entry, schema below).
next_action and reason are optional ROOT keys; no other root keys are allowed.
The controller may request assess_relation after local checks pass and historical evidence is read.
For THAT tool, submit only result, reason, quotes, related_items, links (when linking), and optional next_action.
It compares the earlier claim with the current claim; do not resubmit local fields or the other five checks.
Both old and current quotes are required for a proposed link. No link is also possible: explain why the read
records are unrelated, or remain uncertain and choose further search or a pending stop.
{RULES}

The controller alternates an assess_item call with an allowed lookup. Each assessment submits ALL proposed fields,
six checks (statement, owner, deadline, status, conflict, relation), and may recommend a next_action.
Use only dialogue actually included in observed_dialogue or returned by a lookup in this conversation.
Every pass needs selected supporting dialogue IDs. Do not cite unread lines, invent quotes, or treat a
participant roster as evidence of an assignment. Use the actual speaker/role mappings for this meeting.
Prefer returning IDs only; code copies the authoritative source text. If using a legacy quote object, its text
must be an exact contiguous substring, including repeated words and punctuation; paraphrases are rejected.

CHECKS
- statement: does the dialogue support this exact substantive action/decision/problem, including its scope?
  For Decisions, reject agenda, generic procedure and unaccepted brainstorming. peer_items lists earlier items
  in this run: flag a true duplicate of the same choice, preserving genuinely different scope or constraints.
- owner/deadline: does current dialogue support the claimed person and original deadline wording? An announcer
  is not necessarily the owner. Missing or ambiguous values are uncertain and should be left null. These checks
  are not_applicable only for non-actions.
- status: is the proposed end-of-current-meeting status supported, preserving conditions and uncertainty?
- conflict: pass only when there is no unresolved contradiction in the available evidence; otherwise identify
  affected fields (statement, owner, deadline, status, related_items) and cite the conflicting lines.
- relation: to assert a change to an earlier item, retrieve its recorded ID AND read its supporting dialogue.
  Cite both current change evidence and that prior item's evidence. A search hit alone does not establish an
  update/cancellation/completion. Use not_applicable only for the first meeting, disabled prior context,
  or when no earlier relationship needs checking. Missing records leave uncertainty, not not_applicable.
  Do not rewrite an earlier meeting's historical state.

HISTORICAL RETRIEVAL
Use get_prior_items with a short subject/property query (typically 2-5 content words), not the entire sentence.
The query searches item TEXT, not meeting identifiers. Never use a meeting ID as a search query: the store
already restricts the series and time window. Choose keywords from the item's subject and constraint.
Search matches are candidates, not proof. If the first search is irrelevant or empty, you can change keywords,
remove item_type or search prior dialogue, or stop with a clarification; do not manufacture a relationship.
After finding a candidate ID, read_prior_item reads that record and actual supporting dialogue in one lookup;
use query to focus a component, offset/limit for more evidence, or read_dialogue around a cited line.
Earlier speaker letters are not stable identities. Compare the old claim with the current meeting's actual words.
POSSIBLE_PRIOR or STATUS_CHECK requires an explicit historical check. After reading candidates, compare their
subject, property and commitment with the current one. If the same property now has a narrower/wider scope,
different value or different state, assess that change. If they are genuinely unrelated, pass with an explanation
of the difference and read evidence. If you cannot decide, use uncertain. Do not use not_applicable to skip it.

For EACH proposed related_items entry, checks.relation.links must contain id, relation, changed_fields, reason,
prior_evidence_da_ids and current_evidence_da_ids. Select only IDs whose actual dialogue you read.
Code binds those explicitly selected IDs to the delivered source text if their quotes were omitted; it cannot
read an unobserved ID for you or correct an invented quotation. You may supply exact quotes yourself too.
continues = same task/choice carried forward; changed_fields must be empty.
updates = same subject/property refined (e.g. scope, owner or deadline); identify which fields changed.
replaces = a different choice supersedes the previous one; identify the change.
cancels = current evidence explicitly withdraws the earlier commitment/choice; absence alone is not cancellation.
completes = current evidence establishes an earlier action was finished, not merely presented or discussed.
resolves = an earlier problem was actually settled; reopens = a settled issue/task is explicitly active again.
Do not link two items merely because their words or topics overlap. In particular a new feature is not a
continuation of a broad earlier task unless the evidence supports that relationship. Explain the specific change.
If the earlier record or the change cannot be verified, leave related_items empty and relation uncertain,
and request clarification. Reference the read evidence in the new item; never modify the earlier JSON.
PRIOR_RECORDS_UNAVAILABLE means the earlier system records are missing, not that there is no earlier relation.
You can inspect prior dialogue, but cannot invent a record ID; leave the relationship pending in this situation.

result is pass, fail, uncertain or not_applicable. Give a short evidence explanation, not private reasoning.
NEVER use not_applicable for statement, status or conflict. For conflict, use pass with current dialogue quotes
when no contradiction was found; use fail/uncertain with affected fields otherwise. For an Action, missing owner
or deadline is uncertain, not not_applicable: recommend request_clarification if further evidence cannot settle it.
The proposed fields include owner, owner_basis, deadline, deadline_basis even for non-actions (null/missing).
Use [] related_items when no relationship is established. fields.evidence_da_ids must be read, real dialogue IDs.
The root object contains fields and checks, plus optional next_action/reason. Keep evidence_da_ids and
used_participant_lookup INSIDE fields, never at the root. checks must contain all applicable checks, never {{}}.

NEXT ACTION
accept = all required checks pass. retrieve = lookup can fill an evidence gap; choose a permitted lookup next.
search_again = the first lookup was unhelpful; shorten/change the query or scope. Repeating the same keywords is
not a new search. request_clarification = the available evidence cannot settle an important field or conflict.
stop = further work is unhelpful. Unresolved checks remain pending; never guess to obtain acceptance.
The tool response states the controller's actual decision; follow it. Correct any schema errors named in the
response before submitting another assessment. Queries and the choice of lookup remain yours.
Clarification requests are saved for human review; no person answers during this run.
"""
