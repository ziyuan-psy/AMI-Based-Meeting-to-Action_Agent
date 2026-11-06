# AMI-Based Meeting-to-Action Agent

This project turns an AMI meeting transcript into decisions, action items and
problems, with owners, deadlines, supporting dialogue and links to earlier
meetings. A fixed pipeline handles parsing, extraction, consolidation and
validation. A bounded follow-up agent chooses read-only retrieval tools to
investigate unresolved items.

The outputs include a JSON record in annotation schema 0.5.0, a readable Markdown
report, clarification requests and execution traces. The repository also contains
a local review UI, evaluation scripts and saved experiment artifacts. Outputs are
draft predictions for human review; schema validation does not establish factual
correctness.

## Quick start: browse the saved demo

Clone the complete repository, including its data and saved artifacts:

```powershell
git clone --branch main https://github.com/ziyuan-psy/AMI-Based-Meeting-to-Action_Agent.git
cd AMI-Based-Meeting-to-Action_Agent
.\preview-ui.cmd
```

On Windows, the launcher requires **Python 3.10 or later** and installs the UI
dependencies into `.venv-ui` on first launch. Initial setup needs an internet
connection. It opens [http://127.0.0.1:8765/](http://127.0.0.1:8765/); keep the
terminal open and press Ctrl+C to stop the server. If that port is occupied, use
`.\preview-ui.cmd --port 8766`.

Browsing saved results needs **no API key or model calls**. All 16 development
transcripts are selectable; saved UI records cover TS3006a-d. For a short demo:

1. Open **TS3006d** and inspect **Meeting record**, including owners and missing deadlines.
2. Open **Cross-meeting changes** and select the Morse-code/speech-recognition item.
3. Compare the earlier and current records, then inspect the investigation trail,
   retrieved material and supporting dialogue.
4. Open **Readable report** or use **Export** for Markdown or structured JSON.

The UI labels these as saved draft results. Its `recall_candidate_v1` profile uses
targeted follow-up with **full P2 checks disabled**; relationships still need human
review. See the [UI guide](ui/README.md) for live generation and interface details.

## Dependencies and environment setup

The examples below use **Windows PowerShell from the repository root**. No virtual
environment activation is required.

| Dependency | Purpose |
| --- | --- |
| Python 3.10+ and Git | Run the project and obtain the complete checkout |
| `jsonschema==4.26.0` | Validate records against JSON Schema |
| `openai==3.39.0` | OpenAI-compatible model client for generation and judging |
| `markdown-it-py>=3,<5` | Render reports in the UI |
| `pytest` | Run the agent's offline tests |
| `anthropic` (optional) | Alternative model or evaluation-judge backend |

The shared runtime packages are listed in [ui/requirements.txt](ui/requirements.txt)
and also cover the default CLI backend. Install them and the test runner:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r ui/requirements.txt
.\.venv\Scripts\python -m pip install pytest
```

For macOS/Linux, create the same environment with `python3 -m venv .venv`, then use
`.venv/bin/python` in place of `.\.venv\Scripts\python`. Install the same packages.
Replace PowerShell environment assignments with `export`, for example
`export PYTHONPATH="$PWD/agent/src"` and `export PYTHONUTF8=1`.

The transcript parser uses only the Python standard library. Node.js, a frontend
build, model training and a separate audio download are not required.

## Configure model access

Live generation and LLM-based evaluation require a valid model credential and
incur model usage. Create **`agent/.env`** with your own configuration:

```dotenv
OPENAI_API_KEY=your_gateway_api_key
OPENAI_BASE_URL=https://api.portkey.ai/v1
OPENAI_MODEL=@30875-fall26-openai/gpt-5-nano
```

These URL/model values are the repository's Portkey defaults. The key must be
valid for that gateway and model route. To use another OpenAI-compatible service,
set both `OPENAI_BASE_URL` and `OPENAI_MODEL` for that service. Existing environment
variables take precedence over `agent/.env`. The file is Git-ignored; keep real
credentials out of commits.

For the optional Anthropic backend, install it with
`.\.venv\Scripts\python -m pip install anthropic`, then set `ANTHROPIC_API_KEY`
and an appropriate `MTA_MODEL`. The CLI selects the OpenAI-compatible backend
whenever `OPENAI_API_KEY` is set.

For live runs in the UI, configure `OPENAI_API_KEY`, restart the server and choose
**Run agent**. The UI processes earlier meetings first, shows actual stage
progress and supports **Stop run**. It verifies the accepted runtime and model
configuration before running; see [live UI setup](ui/README.md#final-agent-connection).

To serve the UI manually with the environment created above:

```powershell
.\.venv\Scripts\python -m ui.server --port 8765
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/). This is a local server
on the reviewer's computer, rather than a hosted website.

## Run the agent from the command line

Set these variables in each new PowerShell session used for agent commands:

```powershell
$env:PYTHONPATH = (Resolve-Path .\agent\src).Path
$env:PYTHONUTF8 = "1"
```

Run the four meetings of a development series in chronological order:

```powershell
.\.venv\Scripts\python -m mta.run TS3006 --mode s2 --quality-profile development --out agent/runs/example
```

The series runner processes a, b, c and d in order. S2 reads the earlier system
records from the same output directory. For a single-meeting smoke run, use the
first transcript; later meetings need their earlier outputs for complete history:

```powershell
.\.venv\Scripts\python -m mta.run transcripts/SB/TS3006a.txt --mode s2 --quality-profile development --out agent/runs/single
```

The three experimental modes are:

| Mode | Behavior |
| --- | --- |
| `b0` | One-shot baseline, same output schema, no retrieval tools or earlier meetings |
| `s1` | Full workflow without earlier-meeting context |
| `s2` | Full workflow with earlier transcripts and system records from the same series |

To add the comparison modes to the example run:

```powershell
.\.venv\Scripts\python -m mta.run TS3006 --mode b0 --quality-profile development --out agent/runs/example
.\.venv\Scripts\python -m mta.run TS3006 --mode s1 --quality-profile development --out agent/runs/example
```

Quality profiles are separate from these modes:

| Profile | Intended use |
| --- | --- |
| `development` | Default CLI profile and the profile used in the reported held-out experiment; full P2 checks enabled by default in S1/S2 |
| `recall_candidate_v1` | Accepted UI candidate; targeted follow-up with full P2 disabled |

Select the candidate explicitly with `--quality-profile recall_candidate_v1` and
use a separate output directory. The UI's development acceptance does not establish
the candidate's held-out accuracy.

Default limits per meeting are 80 model attempts, 1,000,000 input/output tokens,
a 20-minute meeting deadline and a 300-second request timeout. Calls and retries
share these limits. See [runtime control](agent/docs/RUNTIME_CONTROL.md) for
overrides and failure handling. A live series may take several minutes per meeting.

### Generated files

With `--out agent/runs/example`, completed S2 results go to
`agent/runs/example/s2/`:

| File | Contents |
| --- | --- |
| `<meeting>.json` | Structured record in schema 0.5.0 |
| `<meeting>_report.md` | Readable summary, decisions, problems and action table |
| `<meeting>.trace.jsonl` | Agent tool choices, observations and outcomes |
| `<meeting>.stages.jsonl` | Extraction, consolidation, selection and stage events |
| `<meeting>.run.json` | Model, settings, source/input hashes, usage and completion status |
| `<meeting>.p2.json` | Effective verification policy and per-item checks |
| `<meeting>.clarifications.json` | Questions left for human review |

Failed or interrupted runs retain traces and checkpoints under `_attempts/`.
Use a new output directory for a new experiment. Extraction/consolidation caches
under `agent/runs/_cache/` can be reused across runs, so consult stage traces and
usage metadata when comparing runtime or cost.

## Data and transcript parsing

The prepared inputs are included; the quick start does not require the original
AMI download. The selected data contains six series with four meetings each:

| Split | Series | Meetings |
| --- | --- | --- |
| Development (SB) | ES2011, IS1008, TS3004, TS3006 | 16 |
| Held-out (SC) | ES2004, TS3007 | 8 |

[`transcripts/`](transcripts/README.md) contains reconstructed TXT inputs by split.
[`agent/data/`](agent/data/) contains the agent's transcript copies, participant/role
lookup and prepared scenario text. [`revised_gold_output/`](revised_gold_output/)
contains evaluation annotations and the annotation schema; the generation pipeline
does not read the gold item labels.

The shared parser expects segmented UTF-8 lines in this form:

```text
[start --> end] speaker (dialogue_act_id): text
```

Parse a transcript without calling a model:

```powershell
.\.venv\Scripts\python transcript_parser.py transcripts/SB/TS3006b.txt --output parsed_transcripts/SB/TS3006b.json
```

The agent calls this parser directly, so intermediate JSON is optional. Existing
parser outputs require `--overwrite`. Arbitrary ASR prose needs a separate format
conversion. See the [transcript guide](transcripts/README.md) for escaping rules,
schema details and regeneration from original AMI dialogue-act JSON.

Use development meetings for demos and tuning. The generation CLI refuses SC
series unless `--allow-sc` is supplied after the design is frozen.

## Evaluation and saved results

The completed study can be inspected without new model calls:

- [Held-out evaluation report](agent/docs/SC_TEST_REPORT.md)
- [Saved B0/S1/S2 predictions, traces and scoring artifacts](agent/results/sc_final/)
- [Strict and tolerant scoring results](agent/results/sc_final/sc_eval.json)
- [S2 full-cost accounting](agent/results/sc_final/s2_full_cost.json)
- [UI candidate acceptance report](agent/results/candidate_acceptance/66a21d7-v1/REPORT.md)

The held-out study used `development`; the UI uses `recall_candidate_v1`. The
current checkout also includes later evaluation changes. Saved source fingerprints
identify the historical executions; the current Git revision is not a guarantee
of the exact historical runtime. New model calls produce a new replication
attempt, and nondeterminism prevents identical scores from being guaranteed.

After generating TS3006 S2 results above, run a **heuristic, offline** comparison:

```powershell
.\.venv\Scripts\python -m mta.evaluate TS3006 --run agent/runs/example --mode s2 --gold revised_gold_output
```

For **LLM-based** alignment and evidence-support judging, configure model access
first. Set the gold path explicitly so scoring does not depend on a developer's
local directory:

```powershell
$env:MTA_GOLD_DIR = (Resolve-Path .\revised_gold_output).Path
.\.venv\Scripts\python -m mta.evaluate TS3006 --run agent/runs/example --mode s2 --gold revised_gold_output --judge
.\.venv\Scripts\python -m mta.support TS3006 --run agent/runs/example --mode s2
```

These write evaluation JSON and an evidence-support sample beside the predictions.
LLM judgments need independent human checks. Repeat the per-series commands for
other development series and modes as needed.

The legacy aggregate scorer, [`agent/scripts/full_eval.py`](agent/scripts/full_eval.py),
expects all 16 development meetings in all three modes **and** benchmark logs in
the run directory's `_timing/` folder. Plain `mta.run` does not create those legacy
logs; use the per-series commands above for ordinary CLI outputs. For actual
per-meeting usage, inspect the generated `.run.json` files.

Held-out scoring uses the separate [`sc_eval.py`](agent/results/sc_final/sc_eval.py)
entry point after all SC predictions exist. It reads `.run.json` for resource
accounting; see the [held-out report](agent/docs/SC_TEST_REPORT.md) for the recorded
protocol. Keep new runs separate from committed experiment artifacts.

## Offline checks

After installing `pytest`, run from the repository root with `PYTHONPATH` and
`PYTHONUTF8` set as above. These checks use local fixtures and mocked transports:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m unittest discover -s ui/tests -v
.\.venv\Scripts\python -m pytest agent/tests -q
```

## Source layout and further documentation

| Path | Purpose |
| --- | --- |
| [`agent/src/mta/`](agent/src/mta/) | Pipeline, extraction, follow-up agent, retrieval, validation and scorers |
| [`transcript_parser.py`](transcript_parser.py) | Shared TXT parser used by the CLI and UI |
| [`scripts/`](scripts/) | Transcript export/preparation tools |
| [`ui/`](ui/README.md) | Local HTTP server, live-run integration and browser interface |
| [`agent/tests/`](agent/tests/), [`tests/`](tests/), [`ui/tests/`](ui/tests/) | Agent, parser and UI tests |
| [`agent/results/`](agent/results/) | Saved studies, source snapshots, predictions and traces |

For implementation details, start with the [agent guide](agent/README.md),
[method description](agent/docs/METHOD_EN.md) and
[P2 verification contract](agent/docs/P2_CONTROL.md). Earlier handoff and design
notes describe intermediate project states; use the current CLI and the completed
evaluation report for run instructions and experiment status.
