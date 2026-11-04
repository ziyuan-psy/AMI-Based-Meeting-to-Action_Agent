# Meeting budgets, timeouts and partial artifacts

`run_meeting` creates one controller for a meeting. Extraction, consolidation, agent turns and abstracts
share its model-attempt limit, token ledger, concurrency limit and deadline. The existing retrieval
limits (4 lookups per item, 30 per meeting) remain separate and are still enforced in code. This change
does not alter extraction prompts, voting thresholds, quantity caps or the final item schema.

## Configuration

Set these variables before starting Python; the effective values are saved in `.run.json`.

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `MTA_MAX_MODEL_CALLS` | 80 | Maximum admitted model attempts per meeting, including failed attempts and all retries. |
| `MTA_MAX_TOTAL_TOKENS` | 1000000 | Combined input/output token accounting limit for the meeting. |
| `MTA_REQUEST_TIMEOUT` | 300 | Seconds allowed for one admitted SDK request; waiting for a slot is covered by the meeting deadline. |
| `MTA_MEETING_TIMEOUT` | 1200 | Seconds from controller creation, immediately before parsing, through publication. |
| `MTA_MAX_INFLIGHT` | `MTA_WORKERS`, otherwise 4 | Maximum simultaneous admitted model requests in this meeting. |
| `MTA_TRANSPORT_RETRIES` | 2 | Additional attempts after transient transport errors; each consumes the same meeting budget. |
| `MTA_RETRY_BACKOFF` | 0.5 | Initial retry delay in seconds, doubled for each subsequent retry. |

Call/token limits and retry counts may be zero. Timeouts and concurrency must be positive; backoff
must be nonnegative. Nonfinite timeouts/backoff and negative limits are rejected before model work.
`MTA_WORKERS` continues to control stage-worker count; a smaller `MTA_MAX_INFLIGHT` can serialize model
requests without changing that count.

For example, from `agent/` in PowerShell:

```powershell
$env:PYTHONPATH = "src"
$env:MTA_MAX_MODEL_CALLS = "60"
$env:MTA_MAX_TOTAL_TOKENS = "600000"
$env:MTA_REQUEST_TIMEOUT = "120"
$env:MTA_MEETING_TIMEOUT = "900"
$env:MTA_MAX_INFLIGHT = "2"
python -m mta.run data/transcripts/TS3006a.txt --mode s2 --out runs/control-dev
```

## Admission, retries and token accounting

Admission is atomic: a request reserves one call, an inflight slot and enough tokens for its conservative
input estimate plus its full configured output cap. The input estimate uses the UTF-8 byte length of
the serialized request plus 1,024 bytes for framing. The existing OpenAI output floor of 32,000 tokens
is included in the reservation; output caps are not reduced to squeeze a request into the budget.
When parallel requests temporarily hold the remaining tokens, another request waits for settlement
instead of immediately declaring the budget exhausted.

Successful responses settle against provider usage and release unused reservation. Anthropic cache-read
and cache-creation input usage is included. When usage is missing, invalid, or unavailable after an error
or timeout, the full reservation remains charged. A provider usage value exceeding the reservation stops
the meeting with `token_accounting_overrun`. This is a conservative local resource ledger, not a dollar
spend limit or a guarantee of the provider's final billing.

For controlled meetings, SDK retries are disabled with `with_options(max_retries=0, timeout=...)`.
The controller retries connection failures, SDK timeouts, HTTP 408/409/429 and 5xx responses, up to the
configured retry count. Other API errors stop immediately. Retry backoff is interrupted by cancellation
or the meeting deadline. Structured-output retries, empty extraction retries, consolidation retries
and repeated agent turns also use the same controller; none receives a separate allowance.
Standalone evaluation/judge clients are outside this meeting controller.

The supervisor also bounds requests that ignore SDK timeouts and local stages that stall. A hard
supervisor request timeout ends the meeting without launching another overlapping retry. Already
accepted provider requests cannot be revoked by this controller: a transport may finish later in a
daemon thread. Its reservation is retained, queued work is cancelled and late writes to caches,
checkpoints, traces and final outputs are fenced. Initial provenance capture and final persistence add
filesystem overhead outside the work deadline; stalled filesystem operations cannot be forcibly killed.

## Status and artifacts

| Status | Typical reason | Result |
| --- | --- | --- |
| `COMPLETED` | Validated record and rendered report published | Final JSON/report and run metadata. |
| `BLOCKED` | Invalid transcript/participants, unusable consolidation, or failed output validation | Partial artifacts; no new final record. |
| `BUDGET_EXCEEDED` | `call_budget_exhausted`, `token_budget_exhausted`, `token_accounting_overrun` | Stop further work; retain completed stages. |
| `TIMED_OUT` | `request_timeout`, `meeting_timeout` | Stop further work; retain completed stages. |
| `FAILED` | `model_request_failed`, `stage_failed`, initialization/configuration/persistence failure | Named failure stage and safe error type/status. |
| `CANCELLED` | `cancelled` | Interrupt processing and retain completed stages. |

The first failure wins, including when parallel tasks fail. Expected agent-policy fallbacks (such as no
valid finish after the permitted turns) remain counted in the report. Unexpected agent/tool/code errors
and exhausted API retries stop the meeting instead of being reported as a completed fallback.
Provider error messages are not stored; error type and HTTP status are sufficient for these failures.

With P2 enabled, unresolved policy outcomes are explicit pending items rather than extractor fallbacks.
Semantic checks and tool-selection turns use this same controller. A COMPLETED meeting can contain pending
fields or quarantined statements; consult `.p2.json` and `.clarifications.json`. These sidecars are published
and rolled back with the record/report, hashed for batch completion checks, and saved as partial artifacts on
interruption. See [P2 control](P2_CONTROL.md) for the per-item state machine.

Every initialized run has an attempt directory:

```text
runs/<mode>/_attempts/<meeting>/<run_id>/
  checkpoints/           # completed parsed/extracted/consolidated/... data; successful agent items individually
  run.json               # final metadata, code/model/config identity and shared budget ledger
  stages.jsonl           # stage snapshots and model admission/settlement/stop events
  agent.trace.jsonl      # agent decisions
  partial.json           # non-COMPLETED runs: index of available checkpoints and this attempt's own traces
  previous/              # any previous canonical record/report and latest sidecars, before replacement
```

The usual `<meeting>.run.json`, `.stages.jsonl` and `.trace.jsonl` expose the latest attempt. Their metadata
contains `runtime_control`: limits, admitted calls, charged/unknown tokens, peak inflight count, terminal
failure and the individual attempts. `usage` reports measured completed responses; unknown reservations
appear in the budget ledger rather than being fabricated as measured provider usage. A cache hit makes
no model request and consumes no model allowance.

Checkpoints are saved atomically as stages finish; a failed parallel stage can still have completed
sub-results in its stage trace. `partial.json` is a development/debug artifact, not a valid final record
and not an automatic resume mechanism. Reruns can reuse successfully committed caches. The existing
series runner verifies a latest `COMPLETED` status and record/report hashes before skipping a meeting;
it stops with a nonzero exit status on any other result, so failed reruns do not silently feed old
outputs into the next meeting.

Only the supervisor publishes a validated record and rendered report. Individual file replacements are
atomic; if either publication fails, previous bytes are restored where filesystem access permits.
The record/report pair is accepted by the runner only after successful metadata finalization. If the
filesystem itself prevents persistence, the returned result includes `artifacts_saved: false` and
`persistence_error_type`; retaining additional artifacts on that filesystem cannot be guaranteed.

## Offline validation

`tests/test_control.py` injects deterministic transports and local-stage failures. It covers concurrent
admission, reservation settlement, all retry paths, hard request and meeting timeouts, cancellation,
partial checkpoints, late-response fencing, publication rollback, batch failure exits and invalid limits.
It makes no provider requests. The previously captured `results/dev_baselines/trace-v1-ts3006ab/` is
preserved unchanged; this implementation has not run a new live baseline or final evaluation.
