# Live development attempts

Every run is preserved with its original code/configuration, inputs, model responses, tool returns and
last checkpoint. These runs span code changes; do not interpret their count as an evaluation success rate.

| Capture | State / reason | Model attempts | Lookups | Known tokens | Unknown-usage charge | Seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| [scenario-v1](scenario-v1/run.json) | pending / invalid_action_limit | 2 | 0 | 11,889 | 0 | 21.46 |
| [scenario-v2](scenario-v2/run.json) | pending / invalid_action_limit | 2 | 0 | 10,964 | 0 | 12.44 |
| [scenario-v3](scenario-v3/run.json) | pending / no_progress | 7 | 2 | 51,513 | 0 | 33.82 |
| [scenario-v4](scenario-v4/run.json) | pending / stop | 6 | 1 | 37,073 | 0 | 26.14 |
| [scenario-v5](scenario-v5/run.json) | accepted / all_required_checks_passed | 3 | 1 | 23,876 | 0 | 62.61 |
| [scenario-v6](scenario-v6/run.json) | FAILED / model_request_failed | 3 | see trace | 15,608 | 70,015 | 55.60 |
| [evaluation-v1](evaluation-v1/run.json) | FAILED / model_request_failed | 3 | see trace | 15,942 | 73,855 | 45.78 |
| [scenario-v7](scenario-v7/run.json) | pending / no_progress | 5 | 2 | 45,913 | 0 | 173.03 |
| [evaluation-v2](evaluation-v2/run.json) | pending / no_progress | 7 | 3 | 69,938 | 0 | 105.18 |

Known tokens are reported input plus output tokens. An unknown-usage charge is a conservative
reservation charged by existing runtime policy, not measured provider token consumption.

## Observations

- **scenario-v1**: Pending: redundant root fields / invalid protocol; no lookup.
- **scenario-v2**: Pending: missing arrays and root documents / invalid protocol; no lookup.
- **scenario-v3**: Pending: found scenario passages but did not resolve selections/status.
- **scenario-v4**: Pending: stopped after unhelpful retrieval; missing meeting-proof selection.
- **scenario-v5**: Accepted: two page-66 ranges retained, unused page-87 hit excluded; no reader call.
- **scenario-v6**: Failed: provider rejected root-level conditional tool schema (HTTP 400).
- **evaluation-v1**: Failed: provider rejected root-level conditional tool schema (HTTP 400).
- **scenario-v7**: Pending: final-code repeat questioned chip consensus, then repeated lookup.
- **evaluation-v2**: Pending: scenario difference identified; document ID misused as dialogue ID and rejected.

The final tool schema has a flat object root. Subsequent live document assessments were accepted by
the gateway. HTTP 400 is non-retryable under the existing policy; the two failed runs preserve partial
results and failure metadata rather than pretending completion.

The current code replays the original provider responses from scenario-v5, scenario-v7 and evaluation-v2.
The saved records, document citations and pending outcomes are reproduced without further API calls.
See [CASE.md](CASE.md) and [verification.json](verification.json) for scope and integrity results.
