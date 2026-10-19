# Architecture V3：对齐已选数据与 gold schema 0.5.0

相对 V2 的主要变化（根据你们的补充和 gold 仓库实际内容）：

| 项目 | V2 | V3 |
|---|---|---|
| 数据 | 泛指 AMI | 6 个 series、24 场（见 §1），SB 开发 / SC 留出评测 |
| 用户流程 | 含 `ask_clarification` | **无法追问**：无法确定的 owner/deadline 直接在输出中标 `ambiguous / missing` 并列入"待确认"章节 |
| 输入 | 内部 JSON | **一个 `.txt` 转写**（`transcripts/` 的 TXT 格式） |
| 输出 | JSON + 若干 md | **可读文档（Markdown/HTML）** + 一份**符合 gold schema 的 JSON**（给评估用） |
| 工具 | 泛化 | 三类 retrieval 工具，与 gold `reference.source_type` 一一对应：`dialogue / participant_metadata / project_document` |
| 日期 | `resolve_date` 换算 ISO | AMI 没有日历日期，gold 规定 **deadline 保留原话，不得编造日期** → 改为"原话分类"，不换算 |
| Demo | 可选自任一 series | SC 不可用于 demo/调参 → **demo 用 SB 的 TS3006**（a→b/c/d） |

---

## 1. 数据与划分

| 划分 | Series | 场次 | 特点（来自你们的选择说明） |
|---|---|---|---|
| SB 开发 | ES2011 | a–d | owner 歧义、共同分配、部分证据 |
| SB 开发 | IS1008 | a–d | action 多，参与者/前序上下文案例 |
| SB 开发 | TS3004 | a–d | 纠正、歧义、证据恢复 |
| SB 开发 | **TS3006** | a–d | action 多，跨会议变化清楚 → **demo** |
| SC 留出 | ES2004 | a–d | summlink 完整 |
| SC 留出 | TS3007 | a–d | summlink 完整 |

**SC 纪律（gold README 规定）**：SC 的转写、标注、review 结论不得用于 prompt、检索、agent 指令、schema/校验规则、debug、demo 选择。冻结后只跑一次。

**同一 series 内，后面的会议可以读前面的会议**（a 不能读任何；b 可读 a；c 可读 a,b；d 可读 a,b,c）。跨 series 严禁读取。

**Gold**（`revised_gold_output/*.json`）**不进入运行时任何输入**，只用于评估。

---

## 2. 输入与输出

**输入**：单个 TXT，格式
```
[start --> end] speaker (dialogue_act_id): text
```
头部 `# Meeting: TS3006b`、`# Split: SB`。`dialogue_act_id` 就是后续 `reference.locator` 的取值，所以 evidence 引用必须用它。speaker 字母（A/B/C/D）**跨会议不稳定**，不能直接当人。

**输出 1：可读文档**（`<meeting>_report.md`）
1. 会议概要（abstract）
2. 决策表（内容、状态、证据）
3. 问题表
4. **Action 表**：Action｜Owner｜Owner 依据｜Deadline（原话）｜Deadline 依据｜状态｜与前序关系｜证据
5. **与前序会议的变化**（相对哪一条、发生了什么）
6. **待确认事项**（owner/deadline 无法确定的条目及原因；因不能追问，仅列出供人工处理）
7. 附录：证据原文片段（按 dialogue-act ID）

**输出 2：结构化 JSON**，与 gold schema 0.5.0 同构，便于直接评估：
- `abstract`：`id, text, type, reference`
- `decisions / problems`：+ `status, prior_context_needed, related_items`
- `actions`：+ `owner(参与者 ID 数组或 null), owner_basis, deadline(原话或 null), deadline_basis`
- `status` 取值：actions：open/in_progress/completed/cancelled/deferred/unknown；decisions：proposed/adopted/conditional/rejected/deferred/unknown；problems：open/resolved/deferred/unknown
- `related_items.relation`：continues/updates/replaces/cancels/completes/resolves/reopens
- `basis`：explicit/inferred/ambiguous/missing（运行时不输出 `unreviewed`）
- 系统自己的 item ID 与 gold ID 不同，评估时需做条目对齐（§6）。

---

## 3. Context Store（retrieval 的数据来源）

只读、按 series 与会议序号过滤。

| Store | 内容 | 对应 `source_type` |
|---|---|---|
| T：转写索引 | 同 series **更早**会议 + 当前会议的所有 dialogue acts（BM25 + embedding 混合检索，按 `dialogue_act_id` 返回） | `dialogue` |
| P：参与者表 | 每场会议 speaker 字母 → 参与者 ID（如 MTD022ID）→ 角色；来自 `ami-corpus/dialogueActs/<meeting>.json` 的 `attributes.participant / attributes.role`（TXT 转写不含这些字段，需预先建表） | `participant_metadata` |
| S：项目文档 | `amiScenario.pdf` 文本（`scenario_text.txt`），按页/节切块，保留页码与小节 | `project_document` |
| R：前序系统输出 | 同 series 更早会议由**本系统**产出的记录（不是 gold） | 作为 `related_items` 候选 |

约束：
- gold、summlink、abstractive 标注都**不入库**（它们是 gold 的来源）。
- 若用 `dialogueActs` 的 `attributes` 做角色，只取 speaker/role/participant，不取标注字段。
- 运行 b 时 R 里要有 a 的**系统输出**，因此同一 series 必须按 a→b→c→d 顺序跑。

---

## 4. 节点流程（输入→处理→输出→转移）

```
N0 读取 TXT
N1 Parse [Code]
N2 Extract [LLM]
N3 Normalize [Code+Rules]
N4 Triage [Code]
N5 Follow-up Agent [Agent] ⇄ N6 Retrieval Tools [Code] ⇄ Context Store
N7 Assemble [Code+LLM]
N8 Validate [Code] ──不过──▶ N9 Revise once [LLM] ──▶ N8
N10 Render Report [Code] → COMPLETED
        任一环节致命失败 → BLOCKED(manual review)
```

### N0 Input
- 输入：TXT 路径。从头部取 `meeting_id` → 推出 `series_id`、`meeting_order`（a=1…d=4）。
- 转移：能解析出合法 `meeting_id` → N1；否则 BLOCKED。

### N1 Parse `[Code]`
- 工具：`transcript_parser.py`（反转义 `\\ \n \r \t`；保留 ID、时间、speaker 顺序；时间异常只告警不修改）。
- 输出：`turns[]`（= dialogue acts）：`{da_id, speaker, start, end, text}`。
- 同时：向 P 表查 `speaker → participant_id, role`，向 T 索引写入当前会议。
- 转移：turns 非空且 `da_id` 唯一 → N2；否则 BLOCKED。

### N2 Extract `[LLM]`
- 输入：turns（分块，重叠 20 条）。
- 处理：抽取四类条目 `abstract / actions / decisions / problems`；每条给 `text`、`evidence_da_ids`（必须是本会议已有 `da_id`）、原话 `owner_cue`、`deadline_cue`、是否像承诺（vs 建议/条件性提议）。
- 输出：`Candidate[]`。
- 转移：schema 合法 → N3；非法重试 1 次，再失败 → BLOCKED；缺证据条目丢弃并记日志；0 候选 → N7。

### N3 Normalize `[Code+Rules]`
- owner cue 规则映射：说话人 `I` → 该 da 的 speaker→participant_id；角色词（"the designer"）→ 本会议 P 表中该角色的 ID；`we / someone / everyone / you` → 标 `ambiguous`（**不扩展为全体**）。
- deadline：保留原话；用词表分类为 `explicit`（如 "by next meeting"）/ `none`；**不换算日期**；工作时长/测试时长/"after lunch" 等不当作 deadline。
- 去重：相邻块重叠导致的重复合并。
- 输出：`NormItem[]`：`owner_ids?`, `owner_basis(初值)`, `deadline_text?`, `deadline_basis(初值)`。
- 转移 → N4。

### N4 Triage `[Code]`
对每个条目生成 flags：

| Flag | 条件 | 说明 |
|---|---|---|
| `OWNER_UNRESOLVED` | owner 为空/ambiguous/角色映射不唯一 | 需要 P 表或前序 |
| `JOINT_OWNER_CHECK` | owner_cue 含多人 / "we" | 区分共同分配与未指明（ES2011 的重点） |
| `DEADLINE_CHECK` | cue 含相对表达 / 与前序同任务不同 | 判断是新 deadline 还是更新 |
| `POSSIBLE_PRIOR` | 与 R 或前序转移中条目相似度超阈值 | 判断 related 关系 |
| `STATUS_CHECK` | 措辞含 "done / finished / drop / instead / no longer" | 可能 completes/cancels/replaces |
| `WEAK_EVIDENCE` | 证据 da 与条目文本相似度低 / 仅 1 条 | 需补证据 |
| `SCENARIO_CHECK` | 条目与项目职责/流程相关（如角色分工） | 查 S 文档 |

- 无 flag → 直接 N7（`prior_context_needed=false`，`related_items=[]`，status 由规则或 N2 给出）。
- 有 flag → N5。

### N5 Follow-up Agent `[Agent]`
对每个有 flag 的条目独立循环。每步 LLM 输出**一个**动作（JSON），代码校验后执行：

```json
{"action":"search_dialogue","args":{"query":"casing material","scope":"prior","k":5},"why":"..."}
```

| 动作 | 工具 | 返回 |
|---|---|---|
| `search_dialogue` | N6 `search_dialogue(query, scope=current|prior, k)` | `[{da_id, meeting_id, speaker, text, score}]` |
| `read_context` | `read_dialogue(meeting_id, da_id, window≤15)` | 邻近 dialogue acts |
| `lookup_participants` | `get_participants(meeting_id)` | `[{speaker_letter, participant_id, role}]` |
| `get_prior_items` | `get_prior_items(type?, topic?, k=3)`（读 R） | 前序系统条目：text、owner、deadline、status、evidence |
| `lookup_scenario` | `search_project_doc(query, k=3)` | `[{page, section, snippet}]` |
| `finish` | — | 输出结论（见下） |

`finish` 的 payload：`owner_ids, owner_basis, deadline, deadline_basis, status, prior_context_needed, related_items[{ref, relation}], reference[{source_type, source, locator}]`。

**循环与转移**
- 工具返回 → 回到 N5 下一步。
- 终止条件（任一）：① `finish`；② 单条目调用 ≥ 4；③ 会议总调用 ≥ 30；④ 连续 2 步 `new_info=false`；⑤ 连续 2 次非法动作。
- 终止时**无法确定的不猜**：owner/deadline 置 `null` 并标 `ambiguous` 或 `missing`；条目进入"待确认事项"。（因为无法追问）
- 全部条目处理完 → N7。

**典型路径（agent 自选）**

| 情况 | 常见路径 |
|---|---|
| 只是 speaker 字母要解析 | `lookup_participants` → `finish`（1 步） |
| owner 是 "we" / 多人 | `read_context`（看谁接话/确认）→ 视情况 `finish(joint)` 或 `finish(ambiguous)` |
| 与前序任务相关 | `get_prior_items` → `read_context(前序证据)` → `finish(updates/replaces/…)` |
| 前序未命中 | 改写 query 做一次 `search_dialogue(prior)` → 仍无则 `finish(无 related)` |
| 涉及角色职责 | `lookup_scenario` → 仅作解释，**不能覆盖实际会议分配** |

**约束写在代码里**：工具白名单；`scope=prior` 与 `get_prior_items` 强制 `meeting_order < 当前` 且同 series；不可重复相同调用；`finish` 引用的 `da_id`/页码必须真实存在，否则退回并计一次无效动作。

### N6 Retrieval Tools `[Code]`
统一返回 `{ok, data, error, new_info}`，每次调用写日志。工具与 `reference.source_type` 的对应：

| 工具 | 产生的 `reference` |
|---|---|
| `search_dialogue/read_dialogue` | `{source_type:"dialogue", source:"ami-corpus/dialogueActs/<meeting>.json", locator:"<da_id>"}` |
| `get_participants` | `{source_type:"participant_metadata", source:..., locator:<记录/属性>}` |
| `search_project_doc` | `{source_type:"project_document", source:"amiScenario.pdf", locator:"p.<页> <小节>"}` |

注意：`reference` 只写**实际用于支持结论**的来源（gold 规定它"不是检索日志"）；检索日志放在 trace 里。

### N7 Assemble `[Code+LLM]`
- 代码按 gold schema 拼装；LLM 只写 `abstract` 与可读文档的概述，不得新增条目。
- 生成 ID：`<meeting>.sys.<type>.<n>`。

### N8 Validate `[Code]`
| 检查 | 条件 |
|---|---|
| schema | 通过 gold schema 的结构、枚举、字段数（abstract 4 / decisions·problems 7 / actions 11） |
| owner/deadline 一致 | explicit/inferred ⇒ owner/deadline 非空；ambiguous/missing ⇒ owner 为 null（deadline missing ⇒ null） |
| reference 存在 | 每个 `da_id`、页码、metadata 记录真实存在 |
| 时序合法 | `related_items` 指向**更早**条目；`reference` 中的会议序号 ≤ 当前 |
| 一致性 | `completes/cancels` 关系与 `status` 不矛盾；状态为"截至本会议结束" |

全过 → N10；否则 → N9。

### N9 Revise once `[LLM]`
只修复指出的字段，不增删条目，不调用工具；修复后再 N8；仍失败 → BLOCKED(manual review，保留草稿与错误列表)。

### N10 Render `[Code]`
生成可读文档与 JSON；将本会议的系统输出写入 R（供下一场使用）；输出 `trace.jsonl`。→ COMPLETED。

---

## 5. 日志（每步一行 JSONL）

`run_id, meeting_id, item_id, step, flags, action, args, observation(da_ids/页码), new_info, budget_left, tokens, latency, outcome`

---

## 6. 评估（对照 gold 0.5.0）

**系统**
- B0：one-shot LLM，同输出 schema，无检索。
- S1：完整流程，**禁用前序检索**（`scope=prior` 与 `get_prior_items` 返回空；P、S 仍可用）。
- S2：完整流程 + 前序上下文。
- （可选）S3：固定顺序全调用，对照 adaptive 的价值。

**条目对齐**：系统条目与 gold 条目按类型 + 语义相似度 + 证据 da_id 重合度匹配（一对一），再由 LLM judge 复核，人工抽查。

| 维度 | 指标 |
|---|---|
| 抽取 | actions / decisions / problems 的 P/R/F1 |
| Owner | 在匹配的 action 上：owner ID 集合完全一致率（共同分配要求集合相同）；owner_basis 一致率；ambiguous/missing 的 P/R |
| Deadline | 原话是否语义一致；deadline_basis 一致率；"误造 deadline"率（gold 为 null 而系统给了值） |
| Status | actions/decisions/problems 状态准确率 |
| 跨会议关系 | `related_items` 的 (目标条目, relation) 对的 P/R；`prior_context_needed` 准确率 |
| **Evidence support** | 对每条 `reference` 判断是否真正支持：条目本身 / owner / deadline / status（分别判）；LLM judge 盲评 + 人工抽检；与 gold 的 reference 重合度 |
| Provenance 有效性 | 引用是否存在（代码）；与 support 对比体现"存在≠支持" |
| 结构有效性 | 一次通过率、修订后通过率、Blocked 率 |
| Agent 行为 | 平均调用、工具分布、预算耗尽率、非法动作率 |

**有/无前序上下文**：S1 vs S2，整体与 `prior_context_needed=true` 的切片分别报告（预期差异集中在切片上）。

**协议**：SB 上设计与调参；SC 冻结后单次运行。SC 的人工补标按冻结协议完成，不得反向影响系统。

---

## 7. Demo（TS3006，SB）

选 TS3006 中 gold 里带 `related_items`（`updates/replaces/cancels/...`）的一组 a→b（或 b→c）。页面/脚本依次展示：
1. 上一场系统记录中的相关 action（owner、deadline、状态、证据）；
2. 本场引发变化的对话片段（`da_id`）；
3. agent 的决策轨迹：flag → 选择的工具 → 返回的证据 → `finish`；旁边放 S1（无前序）的结果对比；
4. 本场最终 action 表（含"与前序关系"列）和"待确认事项"。

再补一个"路径较短"的 case（只查参与者就结束）和一个"预算耗尽→标 ambiguous、列待确认"的 case。

---

## 8. 实现前需要你们确认的点

1. **R（前序系统输出）还是只用前序转移？** 建议两者都开放；若担心前序错误传播，可加一个只读前序转移的版本做消融。
2. **owner 用参与者 ID（如 MTD022ID）**：speaker 字母到 ID 的对应在语料 `dialogueActs` 的 `attributes.participant/role` 里，但仓库 TXT 转写没带这些字段，所以 P 表要在入库时从 `dialogueActs` 预先生成。若你们已有现成的映射表，我改为直接读它。
3. **abstract 类条目是否纳入评估**：gold 含 abstract，但课程重点在 decisions/actions；建议报告但不作为主指标。
4. **gold 状态**：GitHub 仓库里的 gold JSON 已经填了 owner/status 等字段（只有 README 仍写着 unreviewed 草稿）；是否已最终冻结请你们确认。
