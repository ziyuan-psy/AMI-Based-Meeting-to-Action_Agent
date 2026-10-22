# Meeting-review UI

A local, read-only development demo: choose a meeting, inspect its saved S2 record,
follow cross-meeting links and evidence, read the rendered Markdown report, and
export JSON or Markdown. No model calls, credentials, or cloud services are needed.

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
- Records, Markdown, and JSONL traces come from `agent/results/mvp/s2/`. Only the
  selected meeting and earlier meetings of its own series supply run context.
  The UI never reads gold annotation items or the held-out transcripts.
- Results are validated against the canonical `revised_gold_output/revised_gold
  schema template.json` (0.5.0). JSON export is the original flat item array,
  without UI fields. Metadata belongs in the run envelope. Passing the schema
  does not establish factual correctness.
- All views label saved outputs and pending human review. Relationships are agent
  predictions; the existing snapshot has known owner/link errors. An empty link
  list means "no link recorded," not "no project change occurred."
- Retrieved material is reconstructed from **logged IDs**, separately from final
  supporting references. The trace saved at most 12 result IDs per step and did
  not retain complete payloads, ranking, scores, or a faithful observation order.
  The UI does not re-run retrieval or invent those missing details.

## Professor's cross-meeting walkthrough

1. Open TS3006b and inspect the earlier system-generated record.
2. Open TS3006c, then **Cross-meeting changes**. Select an item, such as the saved
   `updates` relationship. The panel shows the earlier/current records, flags,
   tool choices and arguments, resolved retrieved material, and final citations.
3. Open **Meeting record** for the resulting action table; inspect a row's owner,
   deadline, their basis values, and source dialogue with surrounding context.
4. Open **Readable report** and export its Markdown or the schema-aligned JSON.

This demonstrates the interface and a recorded mechanism. These saved links are
not a verified successful agent demonstration. Validate a real changed item and
its owners/evidence before presenting the final two-meeting demo. Record replay
is visibly labeled and does not pretend to run the model live.

## Final-agent connection

`ui/provider.py` defines the **MeetingProvider** boundary. `ui/server.py` accepts a
provider through `make_server(provider=...)`; the frontend only knows these APIs:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/catalog` | Development meeting IDs, order, saved-output availability |
| `GET /api/meetings/{id}` | Parsed transcript, participants, warnings, prior IDs |
| `POST /api/runs` with `{"meeting_id":"TS3006c"}` | Open a run and return its envelope |
| `GET /api/runs/{run_id}` | Retrieve that run's state and artifacts |
| `GET /api/runs/{run_id}/export?format=json` | Download the original record array |
| `GET /api/runs/{run_id}/export?format=markdown` | Download the readable report |

The envelope contains `run_id`, `meeting_id`, `source`, `mode`, `status`, `stage`,
`schema_version`, `schema_valid`, `review_state`, `items`, `earlier_records`,
`events`, resolved `evidence`, `participants`, counts, warnings, report Markdown,
and sanitized report HTML. **Never add these envelope fields to item records.**
`RunBundle` additionally retains the original JSON text for lossless export.

To add live execution:

1. Implement `MeetingProvider` as a live adapter, using
   `agent/src/mta/pipeline.py::run_meeting`. Keep agent imports in that adapter,
   not in the frontend or saved provider. The saved UI currently cannot call an
   LLM, read `.env`, or run evaluation.
2. Allocate isolated run and cache directories; prepare missing earlier meeting
   records chronologically (a → b → c → d), using system outputs, never gold.
   Include model/prompt/schema versions and transcript-content hashes in caching.
3. Run the agent in a background worker. Extend POST to return a queued envelope;
   use the existing GET route for polling or add an event stream. **Frontend
   polling/progress and non-completed-state rendering still need implementation**;
   the current client expects a completed saved bundle. Do not simulate progress.
4. Emit stage events for parsing, extraction, consolidation, follow-up, validation,
   and rendering. Map each event directly to its final `record_id`; the replay
   adapter currently translates `meeting#0` to the first output record.
5. Save full tool observations, provenance, scores/order if relevant, error
   outcomes, and explicit fallback/termination reasons. Keep retrieval distinct
   from supporting citations. Collect per-meeting timing/token/cost deltas.
6. Apply the same schema and chronology gates before marking a run completed;
   surface blocked/error states, full-run resource limits, timeout, and cancellation.
   Human clarification/resume is a separate agent feature, not wired in this UI.

The live adapter is deliberately a documented connection point, not an enabled
stub that silently falls back to saved data. There is no upload, model-comparison
page, gold-answer display, or editing/resume flow in this scope.

## Verification

```powershell
.\.venv-ui\Scripts\python.exe -m unittest discover -s ui/tests -v
.\.venv-ui\Scripts\python.exe -m unittest discover -s tests -v
node --check ui/static/app.js
```

Tests use real SB snapshots to verify schema fidelity, unchanged exports,
chronological visibility, trace-to-record mapping, evidence resolution, missing
outputs, Markdown escaping, and HTTP behavior. No API calls or gold-label reads.
Session run envelopes are kept in memory (at most 32); restarting the local server
requires reopening a meeting record.
