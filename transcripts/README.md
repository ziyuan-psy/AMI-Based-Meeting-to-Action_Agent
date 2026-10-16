# Reconstructed AMI transcript inputs

These TXT files are meeting inputs for the transcript parser and demo. Each meeting has its own file. They were reconstructed mechanically from the selected `ami-corpus/dialogueActs/<meeting>.json` files; the original JSON and revised gold outputs are unchanged.

| Folder | Split | Series | Meetings |
| --- | --- | --- | --- |
| `SB/` | Development | ES2011, IS1008, TS3004, TS3006 | 16 (a–d for each series) |
| `SC/` | Held-out evaluation | ES2004, TS3007 | 8 (a–d for each series) |

These are reconstructed transcripts, not original ASR TXT exports or newly transcribed audio. Existing dialogue-act segmentation is retained so evidence can be aligned with the existing annotations. A demo using these inputs assumes this segmentation; it does not evaluate speech recognition or dialogue-act segmentation.

## TXT format

Files use UTF-8 with LF line endings. Header lines begin with `#`. Each remaining nonempty line represents one original dialogue-act segment:

```text
[starttime --> endtime] speaker (dialogue_act_id): text
```

Example from the SB meeting ES2011a:

```text
[34.27 --> 37.14] A (ES2011a.A.dialog-act.dharshi.1): Here we go .
```

- Times remain the original strings, measured in seconds; no rounding is applied.
- Timing anomalies also remain unchanged. SB verification found an interval whose end precedes its start. The parser emits a `TranscriptTimingWarning` and preserves both timestamps.
- Segment IDs, speaker labels and array order are preserved. The annotation-author component in an ID does not identify the speaker or action owner.
- Spoken text, punctuation, fillers and markers such as `<vocalsound>` and `<gap>` are preserved. Turns are not merged or summarized.
- To keep one segment per line, literal backslashes are escaped as `\\`, LF as `\n`, CR as `\r`, and tabs as `\t`. The parser reverses these four escapes after reading a line. All other characters, including spaces and Unicode, remain unchanged.

## TXT to JSON parser

[`transcript_parser.py`](../transcript_parser.py) reads only the supplied TXT inputs and produces a flat JSON array with the same nine field names as the original dialogueActs files. It restores `id`, `speaker`, `starttime`, `endtime` and `text` exactly, preserving record order and existing gold evidence locators.

The other original dialogueActs fields are not embedded in the TXT:

| Field | Parser output and later enrichment |
| --- | --- |
| `attributes` | `{}`. Participant/role lookup can later supply meeting-specific metadata. Some original attributes also contain annotation information. Do not assume speaker A/B/C/D identifies the same person in different meetings. |
| `startwordid`, `endwordid` | `null`. Exact original IDs require AMI word-alignment metadata. |
| `label` | `null`. This is a human dialogue-act annotation, not a field recoverable from raw transcript text. |

The explicit output contract is [`schemas/transcript.schema.json`](../schemas/transcript.schema.json). The original dialogueActs files use strings for the three fields that are unavailable here; this parser's schema allows `null` for those fields. The schema for revised gold output is unchanged. The parser does not look up, infer or copy annotation labels, participant assignments or word alignments. `# Source:` is provenance text, not a path the parser opens.

Parse one demo input from the repository root:

```powershell
python transcript_parser.py transcripts/SB/TS3006b.txt --output parsed_transcripts/SB/TS3006b.json
```

Or parse all 16 development meetings:

```powershell
python transcript_parser.py transcripts/SB --output parsed_transcripts/SB
```

Batch input reads only `*.txt` directly inside the specified directory; it does not recursively scan both splits. Existing outputs require `--overwrite`. Generated `parsed_transcripts/` outputs and Python caches are ignored by Git. Writing into the original corpus or revised gold folder is refused, including with `--overwrite`.

The agent can call the parser directly:

```python
from transcript_parser import parse_transcript_file

dialogue_acts = parse_transcript_file("transcripts/SB/TS3006b.txt")
```

For text already in memory, call `parse_transcript(text, expected_meeting="TS3006b")`. Headerless inputs can be parsed if each line has the documented segment format. Arbitrary ASR prose without speaker/timestamp/ID prefixes needs a separate normalization step.

Malformed lines, unsupported escapes, duplicate IDs, conflicting speakers/meetings and nonnumeric/nonfinite timestamps raise `TranscriptParseError` with a line number. No malformed segment is silently discarded. UTF-8 BOM and physical CRLF line endings are accepted. CLI errors exit with code 2; the full batch is parsed and destinations checked before any JSON is written. Timing warnings preserve the source values and do not add fields to the JSON.

No abstractive summaries, summlink annotations, revised gold items, owners, deadlines or cross-meeting gold relationships were used to generate these files. Participant and project context remain separate lookup inputs.

## Verify parser behavior

Only Python's standard library is required. Tests use synthetic SB-shaped examples, including malformed input, escaped text, source timing anomalies and CLI output protection:

```powershell
python -m unittest discover -s tests -v
```

## Regenerate

From the repository root, use Python 3.9 or later. Only the standard library is required:

```powershell
python scripts/export_transcripts.py --split SB
```

The default source is the sibling workspace folder `../ami-corpus/dialogueActs`; outputs go to `transcripts/`. For a different source location, pass `--source-dir "PATH/TO/dialogueActs"`.

SC conversion is an explicit, mechanical preparation step:

```powershell
python scripts/export_transcripts.py --split SC
```

Re-running the exporter replaces the selected generated TXT files. It never writes to the source JSON files. Only the six series listed above are exported.

## Held-out boundary

Use `SB/` for parser development, debugging and demos. `SC/` remains held out. Its files were converted with the same formatter after SB verification; SC content must not inform parser changes, prompts, retrieval, validation-rule changes, debugging or demo selection. Checks on SC exports are limited to mechanical integrity, counts and file completeness. SC review notes and gold annotations are not parser inputs. Explicit SC batch parsing, after the parser is fixed on SB, uses `python transcript_parser.py transcripts/SC --output parsed_transcripts/SC`.
