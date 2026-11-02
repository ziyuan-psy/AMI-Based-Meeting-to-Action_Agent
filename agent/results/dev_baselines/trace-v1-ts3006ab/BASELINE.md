# Development baseline

Status: COMPLETED
Execution: live_model
Model: `@30875-fall26-openai/gpt-5-nano`
Base commit: `03e69aa43d022a61bf6db3068e69dfb10c329778`
Source digest: `adbfdc92df2cf28303df072aeedad80540687c8e5d4179ed654a768b7c2cee1e`

The source/ snapshot identifies the actual executed source, including uncommitted changes.
This is a development capture, with no gold scoring or held-out data.

Cache policy: cold at capture start; S1/S2 share extraction/consolidation caches within this bundle.
S2 timing therefore includes cache reuse. It is not a cold end-to-end latency comparison.

| Mode | Meeting | Status | Candidates A/D/P | Final A/D/P | Calls | Tokens (in/out) | Seconds |
|---|---|---|---|---|---|---|---|
| s1 | TS3006a | COMPLETED | 5/8/4 | 5/3/1 | 12 | 54231/69446 | 158.0 |
| s1 | TS3006b | COMPLETED | 8/30/15 | 5/5/2 | 18 | 105515/127417 | 400.7 |
| s2 | TS3006a | COMPLETED | 5/8/4 | 5/3/1 | 1 | 739/1233 | 7.6 |
| s2 | TS3006b | COMPLETED | 8/30/15 | 5/5/2 | 17 | 35639/5586 | 16.8 |

Per meeting, inspect `<mode>/<meeting>.stages.jsonl` for raw extraction attempts, candidate indexes,
each consolidation attempt (raw groups, merged items and per-sample caps), majority-vote clusters,
restored actions and final caps. `.run.json` records code/model/config, input/context hashes and usage.
The existing `.trace.jsonl` remains the agent tool-decision log.

`baseline.json` includes SHA-256 hashes for every saved artifact and source/input snapshot.
API keys and .env files are never part of the capture.
