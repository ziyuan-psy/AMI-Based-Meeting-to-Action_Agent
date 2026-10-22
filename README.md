# AMI-Based-Meeting-to-Action_Agent

## Meeting-review demo

The [local UI](ui/README.md) lets you select development meetings, inspect saved
agent records, explore cross-meeting links and evidence, render the Markdown
report, and export schema-aligned JSON or Markdown.

**Quick preview (Windows):** double-click **`preview-ui.cmd`**.
Requires Python 3.10+; first launch installs
the UI packages automatically and opens the browser. Keep the terminal open.

See the short **[teammate preview guide](PREVIEW.md)** for clone/branch steps.

Open http://127.0.0.1:8765. All 16 development transcripts are available; saved
agent results currently cover TS3006 a–d. Outputs and relationships are visibly
labeled as saved and pending human review. See [the UI integration guide](ui/README.md#final-agent-connection)
for the provider boundary and remaining live-agent wiring.

Agent implementation and CLI: [agent/README.md](agent/README.md).
