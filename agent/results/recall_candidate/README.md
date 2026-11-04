# Recall candidate evidence

`41654ab-v2` is the authoritative validation capture for `recall_candidate_v1`.
It includes the complete-result delivery guard: unread or truncated-away dialogue
cannot authorize a citation. `41654ab-v1` is the earlier zero-API capture retained
for provenance; its 450-test result predates the two added delivery-boundary tests.

The production profile is selected with `--quality-profile recall_candidate_v1`.
See `agent/docs/RECALL_CANDIDATE.md` for commands and assurance limitations.
The saved score is a 16-meeting recorded-response replay. The separately labeled
S2 smoke uses a scripted offline transport, not real model judgements. Neither
capture is a fresh online S2 evaluation, and neither reads the held-out set.

Use `python agent/results/recall_candidate/verify_saved.py` with the installed
project dependencies for independent zero-API verification. It loads the frozen
scoring code and recorded votes within v2, requiring neither a credential nor the
older local experiment directories. `capture.py` is the original capture driver;
its preparation/local-preservation checks additionally reference those cohorts.
