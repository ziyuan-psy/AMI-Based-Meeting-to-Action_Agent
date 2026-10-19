# Proposed Workflow Architecture and Control Pattern (v2)

本文逐节点说明：**输入 → 处理方式/工具 → 输出 → 进入下一步的条件 → 失败/分支去向**。
节点类型：`[Code]` 确定性代码，`[LLM]` 模型调用，`[Agent]` 自适应循环，`[Store]` 数据存储。

---

## 0. 总流程图（文字版）

```
N0 Input
  │
  ▼
N1 Parse Transcript [Code] ──解析失败──▶ END: BLOCKED(parse_error)
  │ ParsedMeeting
  ▼
N2 Extract Candidates [LLM] ──JSON 不合法(重试1次仍失败)──▶ END: BLOCKED(extract_error)
  │ CandidateItems[]   （0 条候选 ─▶ 跳到 N7，输出空记录并标注）
  ▼
N3 Normalize [Code+Rules]
  │ NormItems[]
  ▼
N4 Triage / Ambiguity Check [Code]
  │ 每个 item 得到 flags[]
  ├── flags 为空 ───────────────────────────────┐
  │                                              │
  ▼ flags 非空                                   │
N5 Follow-up Agent Loop [Agent]  ◀──┐            │
  │  选择动作 → N6 工具执行 ─────────┘            │
  │  (循环：每个 item 独立)                        │
  │ 停止：resolved / unresolved / budget          │
  ▼                                              │
N7 Assemble Record [Code+LLM] ◀──────────────────┘
  ▼
N8 Validate [Code]
  ├── 通过 ──▶ N10 Generate Artifacts ──▶ END: COMPLETED
  └── 不通过 ─▶ N9 Revise once [LLM]
                  ├── 再次校验通过 ──▶ N10
                  └── 仍不通过 ──────▶ END: BLOCKED(manual_review)

N6 工具 ◀──读──▶ [Store] Context Store（仅早于当前会议的记录）
所有 N5/N6 决策 ──写──▶ [Store] Decision Log (JSONL)
离线：N10 输出 + Baseline 输出 + AMI gold ──▶ N11 Evaluation
```

---

## 1. 数据对象（节点之间传递的内容）

| 对象 | 字段 |
|---|---|
| `MeetingInput` | `meeting_id`, `series_id`, `meeting_order`, `meeting_date`(可空), `raw_transcript`(AMI XML/JSON 或文本) |
| `ParsedMeeting` | `turns[]`: `turn_id, speaker_id, role, start, end, text`；`participants[]`: `speaker_id, role, name?` |
| `CandidateItem` | `cid, type(decision/action/issue), text, owner_candidate, due_candidate, evidence_turn_ids[]` |
| `NormItem` | `CandidateItem` + `owner_norm, owner_status, due_iso, due_status, is_dup_of?` |
| `ItemState`（agent 运行时状态） | `item, flags[], observations[], calls_used, status(open/resolved/unresolved)` |
| `ResolvedItem` | `NormItem` + 最终 owner/due/status + `change_vs_prior` + `evidence[]` |
| `MeetingRecord` | `meeting_summary, decisions[], actions[], issues[], unresolved_items[], provenance[], agent_trace_ref` |
| `ContextStore` 条目 | 前序会议的 `MeetingRecord`、转写文本索引、参与者/角色表（**不含 gold 标注**） |

---

## 2. 逐节点规格

### N0 Input
| 项 | 内容 |
|---|---|
| 输入 | AMI 会议转写（words/segments XML）+ meeting ID；或纯文本纪要 |
| 输出 | `MeetingInput` |
| 进入 N1 条件 | 文件存在且 `meeting_id` 可在 `data/split.json` 中查到所属 series |
| 否则 | `BLOCKED(input_error)` |

### N1 Parse Transcript `[Code]`
| 项 | 内容 |
|---|---|
| 工具 | transcript parser（XML → turns；按时间合并同一说话人的连续词段；映射 speaker → role） |
| 输入 | `MeetingInput` |
| 输出 | `ParsedMeeting` |
| 进入 N2 条件 | `turns` 非空；每个 turn 有 `turn_id/speaker/text`；`turn_id` 唯一 |
| 失败 | 格式错误 / 空转写 → `BLOCKED(parse_error)`，结束 |
| 副作用 | 将该会议的 turns 建索引（供 `search_transcript`/`read_turns` 使用） |

### N2 Extract Candidates `[LLM]`
| 项 | 内容 |
|---|---|
| 输入 | `ParsedMeeting`（长会议按 turn 窗口分块，如每块 ~150 turns，块间重叠 20 turns） |
| 处理 | 一次结构化抽取：找出 decision / action / issue，并抽取 `owner_candidate`、`due_candidate`（**原样文本，不推断**）及 `evidence_turn_ids` |
| 输出 | `CandidateItem[]` |
| 进入 N3 条件 | JSON 通过 schema；每个 item 至少 1 个存在的 `evidence_turn_id` |
| 分支 | 缺证据的 item → 丢弃并记 log；JSON 不合法 → 重试 1 次，仍失败 → `BLOCKED(extract_error)`；候选为 0 → 直接去 N7（输出空记录 + 说明） |

### N3 Normalize `[Code + Rules]`
| 项 | 内容 |
|---|---|
| 输入 | `CandidateItem[]` + `participants[]` |
| 处理 | ① 人名/角色规范化（"the designer" → `ID`；"I" → 该 turn 的 speaker）② 日期规范化：绝对日期→ISO；"next meeting / next week / Friday"→保留为 `relative` 并存表达式 ③ 去重：同一块重叠区域内相似度 > 阈值的合并 ④ 给 `owner_status`、`due_status` 初值 |
| 输出 | `NormItem[]`，其中 `owner_status ∈ {explicit, ambiguous, missing}`，`due_status ∈ {explicit, relative, ambiguous, missing}` |
| 进入 N4 条件 | 总是进入 |

### N4 Triage / Ambiguity Check `[Code]`
对每个 `NormItem` 按规则产生 flags（可同时多个）：

| Flag | 触发条件 |
|---|---|
| `OWNER_MISSING` | `owner_status = missing` |
| `OWNER_AMBIGUOUS` | owner 是 "someone / we / everyone / you guys / 无法映射到参与者" |
| `DUE_RELATIVE` | `due_status = relative` 且 `meeting_date` 缺失或表达式不能确定性解析 |
| `DUE_MISSING` | action 无 due（注意：AMI 里很多 action 本来就无 due，此 flag 优先级低，只记录，不触发多次工具调用） |
| `POSSIBLE_UPDATE` | 与 Context Store 中前序 action/decision 的 embedding 相似度 > 阈值（轻量检索，不是 agent） |
| `WEAK_EVIDENCE` | evidence turn 数 < 1，或 evidence 文本与 item 文本相似度 < 阈值 |

| 结果 | 去向 |
|---|---|
| `flags = []` | `status = verified_by_rules` → **直接进入 N7**（跳过 agent） |
| `flags ≠ []` | 加入 agent 队列 → **N5**（按优先级：`POSSIBLE_UPDATE` > `OWNER_*` > `DUE_*` > `WEAK_EVIDENCE`） |

### N5 Follow-up Agent Loop `[Agent]`
| 项 | 内容 |
|---|---|
| 输入 | 一个 `ItemState`（item + flags + 已有 observations）、预算计数 |
| 每步处理 | LLM 读取 state，输出**一个**结构化动作（见下表），代码校验后交给 N6 执行 |
| 输出 | 更新后的 `ItemState`；终止时输出 `ResolvedItem` 或 `unresolved` 记录 |
| 回到循环条件 | 动作为工具调用，且预算未耗尽，且未触发无进展规则 |
| 终止条件（任一） | ① agent 调用 `finish`（resolved）② 调用 `ask_clarification`（→ unresolved，写问题）③ 单 item 调用数达 4 ④ 会议总调用数达 30 ⑤ 连续 2 步 observation 无新信息 ⑥ 非法 action（拒绝并计一次调用，连续 2 次非法即终止） |
| 终止后去向 | 全部 item 处理完 → **N7**；还有未处理 item → 取下一个 item 重进 N5 |

**Agent 的动作空间与"选择规则"（提示词中给出的指引，非强制顺序）**

| 当前 flag / 状态 | 倾向动作 | 工具返回后的下一步分支 |
|---|---|---|
| `OWNER_AMBIGUOUS/MISSING` | `get_participants` 或 `read_turns`(当前会议前后窗口) | 找到唯一合理 owner 且有证据 → `finish(inferred)`；候选 ≥2 → `get_prior_actions(topic)`；无线索 → `ask_clarification` |
| `POSSIBLE_UPDATE` | `get_prior_actions` / `get_prior_decisions` | 命中 → `read_turns`(前序证据) 判断关系 → `finish(new/update/reassign/cancel/deadline_change)`；未命中 → 换关键词 `search_transcript(prior)` 一次，再无 → `finish(new)` |
| `DUE_RELATIVE` | `resolve_date` | 可解析 → `finish`；不可解析（无会议日期）→ 标 `due=relative("next meeting")`，`finish` 并保留 relative，不强行换算 |
| `WEAK_EVIDENCE` | `search_transcript(current)` / `read_turns` | 找到更好证据 → 替换并 `finish`；否则降低 confidence，`finish(weak)` |
| 观察结果与 item 冲突（如前序 deadline 与本场不同） | `get_prior_decisions` 取变更原因 | 本场明确改动 → `finish(deadline_change)`；否则 `ask_clarification` |

**代码侧限制（不可被 LLM 绕过）**
- 工具白名单；参数 JSON schema 校验；
- 相同 `(tool, args)` 重复调用 → 直接返回缓存并计入"无进展"；
- 前序记录工具强制 `meeting_order < 当前`；
- 预算：每 item ≤ 4，每会议 ≤ 30；
- 每一步写 Decision Log。

### N6 Tool Execution `[Code]`（被 N5 调用）

| 工具 | 输入 | 输出 | 数据来源 |
|---|---|---|---|
| `get_participants(meeting_id)` | 会议 ID | `[{speaker_id, role, name, turns_count}]` | ParsedMeeting / Store |
| `get_prior_actions(series_id, before_order, topic?, k=3)` | series、当前序号、可选主题 | `[{ref, text, owner, due, status, evidence_turns}]`（含 `ref="ESxxxxa/a2"`） | Context Store |
| `get_prior_decisions(series_id, before_order, topic?, k=3)` | 同上 | `[{ref, text, evidence_turns}]` | Context Store |
| `search_transcript(query, scope, k=5)` | 查询、`current/prior` | `[{meeting_id, turn_id, snippet, score}]` | 转写索引 |
| `read_turns(meeting_id, start, end)` | 窗口（≤ 30 turns） | `[turn]` | 转写 |
| `resolve_date(expr, meeting_date)` | 相对表达式、会议日期 | `{iso, confidence}` 或 `{error: "no_anchor"}` | 规则库 |
| `ask_clarification(item_id, question)` | 问题 | 写入 `unresolved_items`；返回 `ack` | — |
| `finish(item_id, status, owner, due, relation, prior_ref, evidence_ids)` | 最终结论 | 校验 `evidence_ids` 存在后写入 `ResolvedItem` | — |

工具返回统一格式 `{ok, data, error, new_info: bool}`；`new_info=false` 用于判断"无进展"。

### Context Store `[Store]`
| 项 | 内容 |
|---|---|
| 内容 | 同 series **更早**会议的 `MeetingRecord`、转写索引、参与者/角色表 |
| 写入时机 | 某会议 N10 成功后，将其记录写入 store（供后续会议使用） |
| 禁止 | AMI gold 标注；当前会议及之后会议 |
| 消融 | S1 配置下 `get_prior_*`/`search_transcript(prior)` 返回空 |

### N7 Assemble Structured Record `[Code + LLM]`
| 项 | 内容 |
|---|---|
| 输入 | 所有 `ResolvedItem`、未解决项、`ParsedMeeting` |
| 处理 | 代码拼装 `decisions[] / actions[] / issues[] / unresolved_items[] / provenance[]`；LLM **仅**生成 `meeting_summary`（仅基于已解析条目，不得引入新条目） |
| 输出 | `MeetingRecord` 草稿 |
| 进入 N8 条件 | 总是 |

### N8 Validate `[Code]`
| 检查 | 通过条件 |
|---|---|
| `schema_ok` | 符合 JSON schema |
| `required_fields_ok` | 每个 action 有 text、owner（或 status 为 missing/ambiguous）、due（或 status）、evidence |
| `provenance_ok` | 所有 `turn_id`、`prior_ref` 存在；`prior_ref` 早于本会议 |
| `status_consistent` | `verified/inferred` 必须带 evidence；`unresolved` 必须带 reason |
| `summary_grounded` | summary 中提到的条目 ID 均在记录中 |

| 结果 | 去向 |
|---|---|
| 全部为 true | **N10** |
| 任一 false | **N9**，并附带失败原因列表 |

### N9 Revise Once `[LLM + Code]`
| 项 | 内容 |
|---|---|
| 输入 | 草稿 + N8 的失败原因 |
| 处理 | LLM 仅修复指出的字段（不得新增条目、不得调用工具）；修复后回到 N8 |
| 次数 | **仅 1 次** |
| 结果 | 再次 N8 通过 → N10；仍不通过 → `BLOCKED(manual_review)`，保留草稿与失败原因 |

### N10 Generate Follow-up Artifacts `[Code]`
| 输出 | 内容 |
|---|---|
| `record.json` | 完整结构化记录 |
| `minutes.md` | 摘要、决策、议题 |
| `actions.md/csv` | action table：ID、Action、Owner(状态)、Due(状态)、Change vs prior、Evidence、Status |
| `unresolved.md` | 待澄清项 + 问题 |
| `trace.jsonl` | 决策日志 |
| 后续 | 将 `record.json` 写入 Context Store → `COMPLETED` |

### Decision Log `[Store]`
每次 N5 决策、N6 调用、拒绝、终止原因均追加一行（字段：`run_id, meeting_id, item_id, step, flags, action, args, observation_ids, new_info, budget_left, tokens, latency, outcome`）。

---

## 3. 离线评估流程（N11）

| 步骤 | 输入 | 处理 | 输出 |
|---|---|---|---|
| E1 运行三套系统 | Test 会议 | B0（one-shot LLM，同 schema）、S1（无 prior context）、S2（有 prior context） | 三份 `MeetingRecord` |
| E2 条目对齐 | 系统输出 + AMI gold + 手工标注（Gold-Plus） | embedding 匹配 + LLM judge；人工抽查 | 匹配表 |
| E3 指标计算 `[Code]` | 匹配表 | Decision/Action F1；owner/deadline accuracy；歧义 P/R 与错误自信率；change_vs_prior 准确率；schema 通过率 | 指标表 |
| E4 Evidence support `[LLM judge]` | (claim, evidence 文本) | supported / partial / unsupported（盲评）；人工抽检 30 条算一致率 | support rate；"引用存在 vs 引用支持"差距 |
| E5 Agent 行为统计 `[Code]` | `trace.jsonl` | 平均调用数、工具分布、预算耗尽率、非法 action 率 | 行为表 |
| E6 失败分析 `[人工]` | 错误条目 + trace | 归因：抽取 / 检索 / 判断 / 证据 / 预算 / 标注歧义 | 分析表 |

**评估隔离**：gold 只在 E2 起出现；运行时（N0–N10）不可见。

---

## 4. 两个走查示例（含状态流转）

**示例 A：Owner 不明 → 查参与者 → 查前序**
1. N2 抽到 `"someone should look into the casing material"`，owner_candidate=null。
2. N3：`owner_status=missing`。N4：flags=`[OWNER_MISSING, POSSIBLE_UPDATE]`。
3. N5 step1：`get_prior_actions(topic="casing material")` → 命中 `X-a/a2`（owner=ID，due=next meeting）。
4. step2：`read_turns(X-a, 证据窗口)` → 确认任务相同。
5. step3：`finish(status=inferred, owner=ID, relation=update, prior_ref=X-a/a2, evidence=[b:t112, a:t77])`。
6. N7→N8（引用都存在）→ N10。

**示例 B：Deadline 变化 + 预算耗尽**
1. N4：flags=`[DUE_RELATIVE, POSSIBLE_UPDATE]`。
2. step1 `get_prior_decisions` → 命中旧 deadline；step2 `resolve_date("next meeting")` → `no_anchor`；
3. step3 `read_turns` 无明确新日期；step4 `search_transcript(prior)` 无新信息 → 单 item 预算(4)耗尽。
4. 强制终止 → `unresolved`，问题：“Is the deadline still ‘next meeting’ or has it moved?”，并保留旧记录引用。
5. N7 将其放入 `unresolved_items[]`；N8 通过（unresolved 带 reason）。

---

## 5. 节点转移速查表

| 当前节点 | 条件 | 下一节点 |
|---|---|---|
| N0 | 输入合法 | N1 |
| N1 | 解析成功 | N2 |
| N1 | 失败 | BLOCKED |
| N2 | JSON 合法且有候选 | N3 |
| N2 | 候选为 0 | N7 |
| N2 | 重试后仍非法 | BLOCKED |
| N3 | 总是 | N4 |
| N4 | item 无 flag | N7 |
| N4 | item 有 flag | N5 |
| N5 | 调用工具且预算未尽 | N6 → 回 N5 |
| N5 | finish / ask / 预算尽 / 无进展 | 下一个 item 的 N5，全部完成后 N7 |
| N7 | 总是 | N8 |
| N8 | 全部通过 | N10 |
| N8 | 有失败 | N9 |
| N9 | 修复后通过 | N10 |
| N9 | 仍失败 | BLOCKED |
| N10 | 完成 | COMPLETED（并写入 Context Store） |
