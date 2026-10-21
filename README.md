# AMI-Based-Meeting-to-Action_Agent

## Meeting-review demo

The [local UI](ui/README.md) lets you select development meetings, inspect saved
agent records, explore cross-meeting links and evidence, render the Markdown
report, and export schema-aligned JSON or Markdown.

```powershell
python -m venv .venv-ui
.\.venv-ui\Scripts\python.exe -m pip install -r ui/requirements.txt
.\.venv-ui\Scripts\python.exe -m ui.server
```

Open http://127.0.0.1:8765. All 16 development transcripts are available; saved
agent results currently cover TS3006 a–d. Outputs and relationships are visibly
labeled as saved and pending human review. See [the UI integration guide](ui/README.md#final-agent-connection)
for the provider boundary and remaining live-agent wiring.

Agent implementation and CLI: [agent/README.md](agent/README.md).
