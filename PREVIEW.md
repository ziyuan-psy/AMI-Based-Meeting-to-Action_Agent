# Preview the UI on Windows

**Needs:** Python 3.10+ and an internet connection for the first setup. No API key,
Node.js, model account, or separate dataset download is needed.

## 1. Get the UI branch

```powershell
git clone --branch meeting-demo-ui https://github.com/ziyuan-psy/AMI-Based-Meeting-to-Action_Agent.git
cd AMI-Based-Meeting-to-Action_Agent
```

Already cloned? Run `git fetch origin`, then `git switch meeting-demo-ui`.
If you have local edits, save them before switching branches.

## 2. Launch

Double-click **`preview-ui.cmd`** in the repository folder.

The shortcut sets up dependencies and opens **http://127.0.0.1:8765/**.
Keep the terminal window open while reviewing. Press **Ctrl+C** to stop it.
If the port is busy, run `.\preview-ui.cmd --port 8766` in PowerShell.

## 3. Try this

Open **TS3006d** → **Cross-meeting changes** → inspect an earlier record, the
agent's choices, and supporting evidence. Then try **Readable report** and **Export**.

All 16 development transcripts are selectable. Saved agent outputs currently
cover TS3006 a–d from the accepted release and remain draft predictions.

**Optional live generation:** set `OPENAI_API_KEY` in `agent/.env`, restart the
preview, then choose **Run agent**. This calls the model and processes earlier
meetings first. Watch progress or use **Stop run**. The accepted profile uses
targeted follow-up; full P2 checks are disabled. See **Workflow & review**.

The localhost address works on each reviewer's own computer after launch; pushing
the branch does not publish a hosted website. Technical details: [UI guide](ui/README.md).
