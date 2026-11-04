# AMI-Based-Meeting-to-Action_Agent

## Meeting-review demo

The [local UI](ui/README.md) lets you select development meetings, inspect saved
acceptance records, explore cross-meeting links and delivered evidence, render the
Markdown report, and export schema-aligned JSON or Markdown. **Run agent** starts
the accepted workflow with live progress and cancellation when a model key is configured.

**Quick preview (Windows):** double-click **`preview-ui.cmd`**.
Requires Python 3.10+; first launch installs
the UI packages automatically and opens the browser. Keep the terminal open.

See the short **[teammate preview guide](PREVIEW.md)** for clone/branch steps.

Open http://127.0.0.1:8765. All 16 development transcripts are available; saved
agent results currently cover TS3006 a–d. Outputs and relationships are visibly
labeled by source and pending human review. The accepted `recall_candidate_v1`
profile has full P2 disabled; **Workflow & review** shows its actual policy and usage.
See [the UI integration guide](ui/README.md#final-agent-connection) for live setup.

Agent implementation and CLI: [agent/README.md](agent/README.md).
