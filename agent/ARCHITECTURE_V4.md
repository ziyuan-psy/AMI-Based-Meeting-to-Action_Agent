# Architecture V4

在 V3 基础上的两处修改：

1. **输入是 TXT**：用户给 `.txt` 转写 → 仓库里的 `transcript_parser.py` 转成 dialogueActs 风格 JSON → 补参与者信息 → 后续流程都在 JSON 上进行。
2. **给模型的指令里写明输出字段模板**：以仓库 `revised_gold_output/README.md` 的 Fields 部分和 `revised_gold schema template.json` 为准，把字段含义、枚举、必填规则写进 prompt，并用同一份 schema 在代码里校验。

其余沿用 V3：6 个 series / 24 场、SB 开发 / SC 留出、同 series 后面的会议可读前面的会议、无法追问、输出可读文档 + 结构化 JSON、三类 retrieval 工具。

---

## 1. 总流程

```
用户输入：meeting.txt
  │
N0  读取 TXT，确定 meeting_id / series_id / 序号              [Code]
  │
N1  Transcript Parser：TXT → JSON（9 字段）                    [Code: transcript_parser.py]
  │
N1b Participant Enrichment：补 attributes.participant / role   [Code + Lookup]
  │
N2  Extract Candidates（带字段模板的 instruction）             [LLM]
  │
N3  Normalize                                                  [Code + Rules]
  │
N4  Triage / Ambiguity Check                                   [Code]
  │      无 flag ───────────────────────────────┐
  ▼ 有 flag                                      │
N5  Follow-up Agent  ⇄  N6 Retrieval Tools ⇄ Context Store     │
  │                                              │
N7  Assemble Record（按字段模板）  ◀─────────────┘             [Code + LLM]
  │
N8  Validate（schema + 规则）──不过──▶ N9 Revise once ──▶ N8  [Code / LLM]
  │ 通过                                          仍不过 → BLOCKED(manual review)
N10 Render：可读文档 + JSON + trace；写入 Context Store        [Code]
  ▼
COMPLETED
```

---

## 2. 输入处理：TXT → JSON

### N0 读取 TXT
- 输入：`TS3006b.txt`（格式 `[start --> end] speaker (dialogue_act_id): text`，头部有 `# Meeting: / # Split:`）。
- 处理：读头部取 `meeting_id`，推出 `series_id`（如 TS3006）和序号 `a=1…d=4`。
- 转移：`meeting_id` 合法 → N1；否则 BLOCKED。
- 说明：parser 也接受无头部、但每行符合上述格式的输入；若用户给的是没有说话人/时间戳/ID 的自由文本，**需要另一个规范化步骤**，不在本项目范围内（作为限制写进报告）。

### N1 Transcript Parser
- 工具：`transcript_parser.parse_transcript_file(path)`（或 `parse_transcript(text, expected_meeting=...)`）。
- 输出：JSON 数组，每条 9 个字段：
  `id, speaker, starttime, endtime, text`（来自 TXT），`startwordid, endwordid, label = null`，`attributes = {}`。
- 行为：反转义 `\\ \n \r \t`；保留 ID、时间、顺序；时间倒置只发 `TranscriptTimingWarning`；格式错误、重复 ID、非法时间戳 → `TranscriptParseError`（带行号）。
- 转移：成功 → N1b；抛错 → BLOCKED(parse_error)，报告里写出行号。
- 这里的 `id` 就是之后 `reference.locator` 使用的 dialogue-act ID。

### N1b Participant Enrichment
- 原因：parser 的 `attributes` 为空，且 speaker 字母在不同会议里不对应同一个人。
- 输入：`meeting_id` + parsed JSON。
- 处理：从参与者表 P 取该会议的 `speaker 字母 → participant_id, role`，写入每条 `attributes.participant / role`。
- P 表来源：语料 `dialogueActs/<meeting>.json` 的 `attributes.participant` 与 `attributes.role`（预先离线建表）。**只取这两个字段**——原始 attributes 里还有标注信息，不能带入。
- 校验：每个 speaker 字母必须映射到唯一 (participant, role)，否则 BLOCKED(participant_error)。
- 输出：`ParsedMeeting`（enriched dialogue acts）；同时写入转写索引 T。

---

## 3. 给模型的字段模板指令（核心修改）

### 3.1 原则
- **字段含义和填写规则来自 README 的 Fields 部分**，字段枚举与必填规则来自 `revised_gold schema template.json`。
- 模板直接写进 prompt（不要让模型"自己去读文件"），同时用结构化输出/函数调用的 JSON schema 约束；代码再用同一份 schema 校验（N8）。
- prompt 里的 few-shot 示例**只能来自 SB**；SC 的任何内容不进 prompt。
- gold 的条目不作为运行时示例的"答案"，示例可以用 SB 中经人工确认的条目，但需与 SB 评估区分（见 §7 开发与评估隔离）。

### 3.2 模板内容（写入 N2/N5/N7 的 system prompt）

```
你将生成符合以下“输出字段格式模板”的条目。不要新增、删除或重命名字段。

条目类型 type：abstract | actions | decisions | problems
每个类别的必填字段数：abstract 4；decisions、problems 7；actions 11。

字段                 适用类别            含义
id                   全部                稳定条目 ID，格式 <meeting>.sys.s.<n>（如 TS3006b.sys.s.12）
text                 全部                被标注的陈述；保留条件、范围与不确定性
type                 全部                abstract | actions | decisions | problems
reference            全部                实际用于支持该条目的来源列表，每项 {source_type, source, locator}
                                         source_type：dialogue | participant_metadata | project_document
                                         它不是检索日志，只放真正支持结论的来源
owner                actions             被分配者的稳定参与者 ID 数组；多个 ID 表示共同负责；
                                         无法确定则 null
owner_basis          actions             explicit | inferred | ambiguous | missing
deadline             actions             截止时间的原话；没有则 null；不得编造日历日期
deadline_basis       actions             explicit | inferred | ambiguous | missing
status               actions/decisions/  截至本会议结束时的状态：
                     problems            actions：open|in_progress|completed|cancelled|deferred|unknown
                                         decisions：proposed|adopted|conditional|rejected|deferred|unknown
                                         problems：open|resolved|deferred|unknown
prior_context_needed actions/decisions/  解释或验证本条目是否必须依赖更早的会议（true/false）
                     problems            仅查参与者或查 scenario 不算 true
related_items        actions/decisions/  本条目延续或改变的更早条目 [{id, relation}]；
                     problems            relation：continues|updates|replaces|cancels|completes|resolves|reopens；
                                         没有则 []
```

### 3.3 填写规则（同样写进 prompt，来自 README）
- **owner**：宣布分配的人不一定是 owner；"we/someone/everyone" 不要扩展成全体；`explicit/inferred` 必须给出 ID，`ambiguous/missing` 必须为 `null`。
- **deadline**：保留原话。"by next meeting" 是 explicit（即使是相对的）；不要把工作时长、测试时长、预期邮件、开始条件当作截止时间；`explicit/inferred` 需非空，`missing` 必须为 `null`。
- **status**：区分建议/条件性提议与已接受的承诺；不要仅凭后续的展示推断任务已完成；后续的取消或完成不得覆盖前一场的历史状态，而是在后一场的条目里用 `related_items` 记录。
- **reference / prior_context_needed / related_items 三者含义不同**：
  - `reference`：哪些来源支持这条标注；
  - `prior_context_needed`：没有更早会议能否解释/验证；
  - `related_items`：这条改变或延续了更早的哪条记录。
- **时间边界**：只能使用截至本场会议结束可得的信息，不得用后面的会议补前面的 owner/deadline/status。
- **参与者**：用本场会议的 speaker→participant 映射；speaker 字母不跨会议通用；scenario 职责只作解释，不能覆盖实际会议中的分配。
- **运行时禁止值**：`unreviewed`、`unknown` 仅当证据确实无法判断时对 `status` 使用，`*_basis` 不得为 `unreviewed`。

### 3.4 各节点使用方式

| 节点 | 使用的模板部分 |
|---|---|
| N2 抽取 | type 定义 + `text` + 初步 `owner/deadline` 线索 + 证据要求（只用本场存在的 dialogue-act ID） |
| N5 agent | basis 含义、deadline 规则、status 取值、三者区别（reference / prior_context_needed / related_items）、`finish` 的 payload 即模板字段 |
| N7 拼装 | 完整字段表 + 每类必填字段数 |
| N9 修订 | 完整字段表 + N8 报告的具体失败项 |

N5 的 `finish` 返回值直接对应模板字段：`owner, owner_basis, deadline, deadline_basis, status, prior_context_needed, related_items, reference`。

---

## 4. 节点规格（N2–N10）

### N2 Extract `[LLM]`
- 输入：enriched JSON（分块，重叠 20 条）+ 3.2/3.3 的模板指令。
- 输出：`Candidate[]`：`type, text, evidence_da_ids, owner_cue(原话), deadline_cue(原话), is_commitment`。
- 转移：schema 合法且每条证据 ID 真实存在 → N3；非法重试 1 次，仍失败 → BLOCKED；缺证据条目丢弃并记日志；0 候选 → N7。

### N3 Normalize `[Code+Rules]`
- owner cue → 参与者 ID：说话人 "I" → 该 dialogue act 的 `attributes.participant`；角色词 → 本场该角色的 ID；"we/someone/everyone" → `ambiguous`。
- deadline：保留原话，只分类 explicit / none；不换算日期。
- 去重分块重叠产生的重复条目。
- 输出：`NormItem[]`（`owner_ids?, owner_basis 初值, deadline_text?, deadline_basis 初值`）。

### N4 Triage `[Code]`
Flag：`OWNER_UNRESOLVED`、`JOINT_OWNER_CHECK`、`DEADLINE_CHECK`、`POSSIBLE_PRIOR`、`STATUS_CHECK`、`WEAK_EVIDENCE`、`SCENARIO_CHECK`（条件同 V3）。
- 无 flag → N7（`prior_context_needed=false`，`related_items=[]`）。
- 有 flag → N5。

### N5 Follow-up Agent `[Agent]`
每步输出一个 JSON 动作，代码校验后调用 N6：

| 动作 | 工具 | 用途 |
|---|---|---|
| `search_dialogue` | `search_dialogue(query, scope=current|prior, k)` | 检索转写 |
| `read_context` | `read_dialogue(meeting_id, da_id, window≤15)` | 读邻近对话 |
| `lookup_participants` | `get_participants(meeting_id)` | speaker→ID/角色 |
| `get_prior_items` | `get_prior_items(type?, topic?, k=3)` | 读前序系统输出（R） |
| `lookup_scenario` | `search_project_doc(query, k=3)` | 查 `amiScenario.pdf` |
| `finish` | — | 提交模板字段 |

- 终止：`finish`；单条目调用 ≥ 4；会议总调用 ≥ 30；连续 2 步无新信息；连续 2 次非法动作。
- 不能追问：终止时无法确定 → owner/deadline 置 `null`，basis 标 `ambiguous/missing`，进入"待确认事项"。
- 限制在代码里：工具白名单；`scope=prior` 与 `get_prior_items` 强制同 series 且序号 < 当前；禁止重复调用；`finish` 引用的 ID/页码必须存在。

### N6 Retrieval Tools `[Code]` 与 Context Store

| Store | 内容 | `reference.source_type` |
|---|---|---|
| T 转写索引 | 同 series 更早会议 + 当前会议的 enriched JSON（BM25 + embedding） | `dialogue` |
| P 参与者表 | 每场 speaker → participant、role | `participant_metadata` |
| S 项目文档 | `amiScenario.pdf` 文本（按页/节切块） | `project_document` |
| R 前序系统输出 | 同 series 更早会议由本系统产出的记录 | 用于 `related_items` |

- gold、summlink、abstractive 标注**不入库**；前序会议的转写在使用前同样先过 N1/N1b。
- 因此同一 series 必须按 a→b→c→d 顺序处理，b 需要 a 的系统输出已写入 R。
- 工具统一返回 `{ok, data, error, new_info}`，每次调用写日志；检索日志放 trace，不写进 `reference`。

### N7 Assemble `[Code+LLM]`
- 代码按 3.2 模板拼装；条目 ID 为 `<meeting>.sys.s.<n>`（与 schema 的 ID 正则兼容，`related_items` 才能引用）。
- LLM 只写 abstract 条目和可读文档的概述，不得新增 actions/decisions/problems。

### N8 Validate `[Code]`

| 检查 | 条件 |
|---|---|
| schema | 通过 `revised_gold schema template.json`；各类别字段数正确（4/7/11） |
| basis 与值一致 | `explicit/inferred` ⇒ owner/deadline 非空；owner `ambiguous/missing` ⇒ `null`；deadline `missing` ⇒ `null` |
| 运行时枚举 | 不出现 `unreviewed` |
| reference 存在 | 每个 dialogue-act ID、metadata 记录、文档页码真实存在，且会议序号 ≤ 当前 |
| 时序合法 | `related_items` 只指向更早（或本场更早）的系统条目 |
| 一致性 | `completes/cancels` 与 `status` 不矛盾 |

全过 → N10；否则 → N9。

### N9 Revise once `[LLM]`
- 只修复 N8 指出的字段，不增删条目、不调用工具；修复后回 N8；仍失败 → BLOCKED(manual review)。

### N10 Render `[Code]`
- **可读文档** `<meeting>_report.md`：概要；决策表；问题表；Action 表（Owner｜依据｜Deadline 原话｜依据｜状态｜与前序关系｜证据）；与前序会议的变化；待确认事项；证据原文附录（按 dialogue-act ID）。
- **JSON** `<meeting>.json`：模板同构，供评估。
- `trace.jsonl`；并把本场系统输出写入 R。

---

## 5. 日志
每步一行：`run_id, meeting_id, item_id, step, flags, action, args, observation_ids, new_info, budget_left, tokens, latency, outcome`；另记录 parser 的警告（如时间倒置）。

---

## 6. 评估
系统：B0（one-shot，同模板，无检索）、S1（禁用前序检索）、S2（完整）、可选 S3（固定顺序全调用）。
对照 gold：先条目对齐（类型 + 语义 + 证据 ID 重合），再比较：

| 维度 | 指标 |
|---|---|
| 抽取 | actions / decisions / problems 的 P/R/F1 |
| Owner | ID 集合是否完全一致；`owner_basis` 一致率；ambiguous/missing 的 P/R |
| Deadline | 原话语义一致；`deadline_basis` 一致率；误造 deadline 率 |
| Status | 三类状态准确率 |
| 跨会议 | `related_items`（目标条目 + relation）P/R；`prior_context_needed` 准确率 |
| Evidence support | `reference` 是否真正支持条目 / owner / deadline / status（分别判；LLM judge 盲评 + 人工抽检） |
| Provenance 有效性 | 引用存在率（代码），对比 support 体现"存在≠支持" |
| 结构有效性 | 一次通过率、修订后通过率、Blocked 率；parser 报错率 |
| Agent 行为 | 平均调用数、工具分布、预算耗尽率 |

S1 vs S2 的对比单独看 `prior_context_needed=true` 的切片。

---

## 7. 开发与评估隔离
- SB（ES2011、IS1008、TS3004、TS3006）：开发、调 prompt、选 few-shot、demo（TS3006）。
- SC（ES2004、TS3007）：冻结后单次运行；其转写、gold、review 结论不用于 prompt、检索、agent 指令、schema/校验规则、debug、demo 选择。
- 建议对 SB 的评估只作为开发参考，不作为最终结果；若 SB 的条目被用作 few-shot，评估时需排除这些条目。
- gold 不进入运行时任何输入。

---

## 8. Demo（TS3006，SB）
用户视角：上传 `TS3006b.txt` → 得到可读报告。展示：
1. 上一场（a）系统记录中相关 action；
2. 本场（b）引发变化的对话（dialogue-act ID）；
3. N1/N1b 的结果（TXT→JSON、speaker→ID）以及 agent 的 flag → 工具选择 → 检索证据 → `finish` 轨迹；旁边是 S1（无前序）的结果；
4. 最终 action 表（含"与前序关系"）与"待确认事项"。
再补一个短路径 case（只查参与者）和一个预算耗尽后标 `ambiguous` 的 case。

---

## 9. 待确认
1. 参与者表由 `dialogueActs` 的 `attributes` 离线生成——是否已有现成表可直接用？
2. few-shot 示例从 SB 的哪几条取，评估时如何排除？
3. abstract 条目是否计入主指标。
4. gold 当前是否已经冻结。
