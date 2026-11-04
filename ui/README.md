# Meeting-review UI

A local demo connected to the accepted `agent-development` release. Browse its
saved acceptance records without credentials, or explicitly start a live Agent
run. Both paths share the same evidence, cross-meeting, report and export views.

## Run

**Windows shortcut:** double-click `preview-ui.cmd` at the repository root.
The script prepares dependencies and opens
the browser. See [PREVIEW.md](../PREVIEW.md) for the short teammate instructions.
Keep its terminal window open while reviewing; Ctrl+C stops the server.

Manual setup, if preferred:

From the repository root (Python 3.10+):

```powershell
python -m venv .venv-ui
.\.venv-ui\Scripts\python.exe -m pip install -r ui/requirements.txt
.\.venv-ui\Scripts\python.exe -m ui.server
```

Open **http://127.0.0.1:8765**. Use `--port 8766` if the port is occupied. Stop the
server with Ctrl+C. The server binds only to loopback. It serves an allowlist of
UI assets and API endpoints, not the repository or an arbitrary file directory.
The frontend uses plain HTML/CSS/JavaScript; there is no Node build or CDN.

## Data and scope

- All 16 **SB development transcripts** are selectable: ES2011, IS1008, TS3004,
  TS3006, meetings a–d. The existing root `transcript_parser.py` is called, then
  participant metadata is added separately.
- Only **TS3006 a–d** currently have saved agent records. Other meetings expose
  their transcripts and an explicit "no saved record" state. No output is invented.
- Saved records, reports and traces come from
  `agent/results/candidate_acceptance/66a21d7-v1/run/s2/`. Only the
  selected meeting and earlier meetings of its own series supply run context.
  The UI never reads gold annotation items or the held-out transcripts.
- Results are validated against the canonical `revised_gold_output/revised_gold
  schema template.json` (0.5.0). JSON export is the original flat item array,
  without UI fields. Metadata belongs in the run envelope. Passing the schema
  does not establish factual correctness.
- All views label saved outputs and pending human review. Relationships are agent
  predictions; the existing snapshot has known owner/link errors. An empty link
  list means "no link recorded," not "no project change occurred."
- Retrieved material uses the actual **delivered tool results**, in logged order,
  separately from final citations. The explicit `assemble.item_record_map` binds
  trace items to published records; a removed item is never mapped by position.
- The accepted profile is **`recall_candidate_v1`**, runtime reference **66a21d7**.
  It passed development acceptance with documented limitations. **Full P2 is
  disabled**, including its full conflict, relationship-witness and document
  publication gates. The UI says Draft candidate and does not interpret empty
  P2/clarification sidecars as evidence that every item passed checks.

## Professor's cross-meeting walkthrough

1. Open TS3006b and inspect the earlier system-generated record.
2. Open **TS3006d** (the default), then **Cross-meeting changes**. The saved
   `replaces` relationship records speech recognition being dropped in favor of
   Morse code. Inspect the earlier/current records, flags, tool choices and
   arguments, delivered retrieved material, and final citations.
3. Open **Meeting record** for the resulting action table; inspect a row's owner,
   deadline, their basis values, and source dialogue with surrounding context.
4. Open **Readable report** and export its Markdown or the schema-aligned JSON.

These are real recorded acceptance runs, not an independent correctness judgment
of every relationship. Review the selected case before presenting it. The UI
distinguishes Saved acceptance run from Live agent result. TS3006c has no recorded
historical links in this release; the UI displays that empty state faithfully.

## Final-agent connection

The connection is implemented in `ui/jobs.py` and `ui/agent_worker.py`.
**Run agent** shows the selected meeting and required earlier sequence before
starting. It calls the native `mta.pipeline.run_meeting` with the explicit accepted
profile. Model calls occur only after the user starts a live run.

For live use, set `OPENAI_API_KEY` in the server environment or **`agent/.env`**,
then restart the server. The pinned OpenAI SDK is installed by the Windows
shortcut. Do not enter keys into the UI. The model, effective configuration and
generation runtime hash set must match the frozen study; a mismatch fails preflight instead
of silently selecting another experiment. The default `development` profile is
not the accepted release and is not exposed as a demo toggle.

The checkout's offline scorers (`agent/scripts/full_eval.py` and
`agent/src/mta/evaluate.py`) can evolve for held-out evaluation. Live UI runs
still use and verify their original frozen copies from the accepted snapshot.

Each live job gets a new directory under **`agent/runs/ui/<job_id>/`**, with its
own output, cache, results, worker log and status. Earlier meetings run a→selected
using only this job's system outputs; saved acceptance outputs are never seeded
into live history. One job runs at a time. Closing a tab does not discard it: its
`?job=<id>` URL reconnects to persisted status after reload.

The worker uses an ignored private runtime under `agent/runs/ui-runtime/`:
checkout sources must match the frozen snapshot's content (allowing only Git
newline conversion), and the copied runtime must match its exact byte hashes.
This resolves Windows checkout line-ending differences **without rewriting the
Agent or acceptance package**. Only the 16 development transcripts, participant
metadata and scenario text are copied as inputs; no labels or held-out inputs.
Prompts, resource limits and publication rules are unchanged. A driver hash
identifies the UI orchestration separately from the frozen Agent source.

Polling shows native stage events and current meeting, not a fabricated percent.
**Stop run** signals the existing Agent controller's cancellation mechanism.
Its normal failure/partial-artifact path retains checkpoints. A watchdog stops
an unresponsive worker; interrupted and failed jobs cannot export a final result.
Completed records must pass schema, four-artifact hash/identity and chronology
checks before the UI serves them. Current model requests may still incur usage
when cancellation is requested.

The frontend consumes these APIs:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/catalog` | Development meeting IDs, order, saved-output availability |
| `GET /api/meetings/{id}` | Parsed transcript, participants, warnings, prior IDs |
| `POST /api/runs` with `{"meeting_id":"TS3006d"}` | Open a saved acceptance record |
| `GET /api/agent` | Capability/setup state and accepted policy; no secrets |
| `POST /api/agent/runs` with `{"meeting_id":"TS3006d"}` | Start a fresh live sequence; returns 202 and job ID |
| `GET /api/agent/runs/{id}` | Actual job state, stage transitions, elapsed time |
| `POST /api/agent/runs/{id}/cancel` with `{}` | Request cancellation |
| `GET /api/runs/{run_id}` | Retrieve that run's state and artifacts |
| `GET /api/runs/{run_id}/export?format=json` | Download the original record array |
| `GET /api/runs/{run_id}/export?format=markdown` | Download the readable report |

The envelope contains `run_id`, `meeting_id`, `source`, `mode`, `status`, `stage`,
`schema_version`, `schema_valid`, `review_state`, `items`, `earlier_records`,
`events`, resolved `evidence`, `participants`, counts, warnings, report Markdown,
and sanitized report HTML. `workflow` contains effective policy, sidecar state,
actual per-meeting usage, budget ledger summary and stage transitions.
**Never add these envelope fields to item records.**
`RunBundle` additionally retains the original JSON text for lossless export.

Live failures never fall back to saved results while claiming success. A saved
record may remain visible during a live run and is explicitly labeled. There is
no upload, model comparison, gold-answer display or clarification answer/resume
flow. Human review questions are display-only; no messages are sent to teammates.

## Verification

```powershell
.\.venv-ui\Scripts\python.exe -m unittest discover -s ui/tests -v
.\.venv-ui\Scripts\python.exe -m unittest discover -s tests -v
node --check ui/static/app.js
```

Tests use real acceptance artifacts and simulated workers to verify schema/hash
fidelity, unchanged exports, chronology, explicit trace mapping, delivered-result
order, cancellation, failure paths, isolation and HTTP behavior. They do not make
model calls, read gold labels or claim a new quality score. Live preflight can be
checked independently without a model request; no new live acceptance campaign
is required merely to preview the integrated UI.
