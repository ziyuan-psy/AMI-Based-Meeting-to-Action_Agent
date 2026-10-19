# Meeting-to-Action Workflow v2（根据老师反馈修订）

## 0. 修改总览

| 老师意见 | v1 | v2 的改动 |
|---|---|---|
| 数据集：定义测试集 | 笼统使用 AMI | 按 **meeting series** 切分 dev/test，整套 series 不进开发；测试子集手工标注 owner / deadline |
| 架构：需要 adaptive agent | 固定流水线 | 保留五阶段**骨架**，把"歧义处理 + 上下文验证"改为 **agent 循环**：自选查哪条记录、是否再搜、是否追问、何时停止；工具和调用上限由代码控制，每步决策写日志 |
| 评估：增加准确性检查 | 只检查引用是否存在 | 增加 evidence support、owner accuracy、deadline accuracy；对比 **有 / 无 prior-meeting context** |
| Demo：展示跨会议变化 | 单会议 | 同一 series 两场会议，action/decision 发生变化；展示旧记录 → 检索证据 → agent 的 follow-up 选择 → 新 action table |

---

## 1. 数据集与测试集设计

**数据源**：AMI scenario meetings。同一 series（如 ES2002a–d）为同一 4 人小组（PM/ME/UI/ID）连续 4 次会议，天然有"前序会议 → 后序会议"的关系，并带 abstractive summary、decisions、problems、actions 及其与 dialogue act 的 links。

**切分原则（按 series，不按 meeting）**

| 集合 | 用途 | 规模建议 | 规则 |
|---|---|---|---|
| Dev | 写 prompt、调工具、调 agent 策略 | 2–3 个 series | 随便看、随便调 |
| Test | 最终评估，**只跑一次** | 2 个 series（共 8 场会议）；其中 4–6 场做人工精标 | 整套 series 开发期不得查看或用于 few-shot |
| Demo | 演示用 | 取自 Dev 或 Test 中 1 个 series 的相邻两场 | 若取自 Test，演示在评估完成之后 |

> 具体 series ID 在项目第 1 周确定并写进 `data/split.json`（写死、提交 git），避免"看了结果再选"的嫌疑。选 series 时要求：每场至少有若干 action 标注，且至少一个 series 含明显的 action/decision 变更（供 demo 与 context 消融使用）。

**人工标注子集（Gold-Plus）**

AMI 自带的 action 标注是 abstractive summary 里的"actions"句子及其链接，**不含**规范化的 owner 和 deadline。因此在测试子集上手工补标：

```json
{
  "meeting_id": "ESxxxxb",
  "item_id": "a3",
  "type": "action | decision | issue",
  "text": "...",
  "owner": {"value": "UI", "status": "explicit | ambiguous | missing"},
  "due":   {"value": "next meeting | 2007-.. | null", "status": "explicit | relative | ambiguous | missing"},
  "evidence_turns": ["turn_112", "turn_113"],
  "prior_link": {"meeting_id": "ESxxxxa", "item_id": "a2", "relation": "new | update | reassign | deadline_change | cancel | complete"}
}
```

- 每个条目标：owner、due、证据 turn、与前序会议的关系（`prior_link`）。
- 两位成员各标一半，另标 ~20% 重叠，计算 Cohen's κ 作为标注可靠性。
- 目标规模：约 40–60 个 action + 15–20 个跨会议变更条目。"可管理"即可，不追求全量。
- 角色 → 人名映射：AMI 用 speaker ID 和角色（PM/ME/UI/ID），owner 以 **speaker/role 规范化后的值**为准。

---

## 2. 新架构：固定骨架 + 自适应 Agent

### 2.1 总体

控制模式仍满足课程要求（extraction → normalization → ambiguity check → context verification → structured output），但中间两步改为由 agent 驱动的 **follow-up loop**：

```
Parse (code)
  → Extract candidates (LLM)
  → Normalize (code + rules)
  → Triage (code)：给每个 item 打 issue flag
  → ┌── Follow-up Agent Loop (LLM 选动作, code 执行与限流) ──┐
    │  对每个有 flag 的 item：                               │
    │  observe → choose action → call tool → update state    │
    │  直到 resolved / flagged_unresolved / budget exhausted │
    └────────────────────────────────────────────────────────┘
  → Assemble record (code + LLM)
  → Validate (code) → revise once → Output / Blocked
```

固定的是"必经阶段"，自适应的是"每个 item 需要哪种核查、核查几次"。

### 2.2 Triage：issue flag 决定 agent 的起点

| Flag | 触发条件 | 典型处理路径 |
|---|---|---|
| `OWNER_MISSING / OWNER_AMBIGUOUS` | 无 owner，或"someone / we / you guys" | → 查参与者与角色 → 查前序会议同类任务的 owner → 仍不明则 ask/flag |
| `DUE_RELATIVE` | "next meeting / by Friday" | → 用会议元数据解析成日期或"下次会议"；无法解析则 flag |
| `DUE_CONFLICT` | 与前序记录 deadline 不同 | → 取回前序 decision 与相关 turn，判断是更新还是新任务 |
| `POSSIBLE_UPDATE` | 与前序 action 语义相似 | → 取回前序 action 记录，判断 new / update / reassign / cancel / complete |
| `WEAK_EVIDENCE` | 证据 turn 过少或相关度低 | → 回读 transcript 窗口，或重新检索 |

没有 flag 的 item 直接通过，**不调用 agent**（省成本，也体现"自适应"）。

### 2.3 Agent 可用工具（均为只读、白名单）

| 工具 | 作用 | 数据范围 |
|---|---|---|
| `get_participants(meeting_id)` | 参与者、角色、发言量 | 当前及更早会议 |
| `get_prior_actions(series_id, before_meeting, filter?)` | 前序会议的 action 记录 | 仅 **早于当前会议** |
| `get_prior_decisions(series_id, before_meeting, topic?)` | 前序 decision | 同上 |
| `search_transcript(query, scope=current|prior, k)` | 在转写文本中检索 | 同上 |
| `read_turns(meeting_id, start_turn, end_turn)` | 读取具体对话窗口 | 同上 |
| `resolve_date(expr, meeting_date)` | 相对日期解析（确定性） | — |
| `ask_clarification(item_id, question)` | 生成待澄清问题，写入 unresolved 队列（离线评估中不真正等待回答） | — |
| `finish(item_id, status, evidence_ids)` | 结束该 item | — |

**防泄漏**：工具层在代码里强制 `meeting_order < current`，agent 无法读到当前或未来会议的 gold 标注。AMI gold 标注**绝不**进入运行时 context store，只用于离线评估。

### 2.4 Agent 循环与控制

每一步 agent 输出结构化动作：

```json
{"thought_summary": "owner 是 'someone'，先看前序会议里同主题任务归谁",
 "action": "get_prior_actions",
 "args": {"series_id": "ESxxxx", "before_meeting": "ESxxxxb", "topic": "remote casing"},
 "expected_gain": "找到 owner 候选"}
```

**代码侧硬限制（不交给 LLM）**

| 项目 | 上限 |
|---|---|
| 每个 item 的工具调用 | ≤ 4 次 |
| 每场会议总调用 | ≤ 30 次 |
| 同一工具 + 相同参数 | 禁止重复（返回 cached / 拒绝） |
| 允许的 action 集合 | 白名单；非法 action → 记录并拒绝，计一次调用 |
| 只读 | 无写入、无外部网络 |
| 停止条件 | `finish` / 预算耗尽 / 连续两次无新证据 → 强制转 `unresolved` |

**停止规则（"何时停")**：owner 与 due 均达到 verified 或明确 flagged；或预算耗尽。预算耗尽时**不得编造**，必须输出 `unresolved` 并附原因。

### 2.5 决策日志（每步一条，JSONL）

```json
{"run_id":"r12","meeting_id":"ESxxxxb","item_id":"a3","step":2,
 "flag":"OWNER_AMBIGUOUS","state_summary":"owner_candidate=null",
 "action":"get_prior_actions","args":{...},
 "observation_ids":["ESxxxxa/a2"],"tokens":812,"latency_ms":950,
 "budget_left":{"item":2,"meeting":25},
 "outcome":"owner_candidate=UI (support: turn_77)"}
```

日志用于：(1) 失败分析；(2) 统计 agent 行为（平均调用数、各工具使用率、预算耗尽率）；(3) demo 展示。

### 2.6 输出 schema（保留并加强 provenance）

```json
{
  "meeting_id": "...",
  "actions": [{
    "id": "a3", "text": "...",
    "owner": {"value":"UI","status":"verified|inferred|ambiguous|missing","source":"explicit_turn|prior_record|participant_lookup"},
    "due":   {"value":"...","status":"explicit|resolved|ambiguous|missing"},
    "change_vs_prior": {"relation":"new|update|reassign|deadline_change|cancel","prior_ref":"ESxxxxa/a2"},
    "evidence": [{"meeting_id":"...","turn_ids":["turn_112"],"quote":"...","supports":["text","owner","due"]}],
    "confidence": 0.0
  }],
  "decisions": [...], "issues": [...],
  "unresolved_items": [{"item_id":"..","reason":"..","question":".."}],
  "agent_trace_ref": "logs/r12.jsonl"
}
```

Validator（代码）检查：schema 合法；必填字段齐全；每个 evidence 的 turn_id **真实存在**；`prior_ref` 真实存在且早于当前会议；status 与字段一致（如 `verified` 必须有 evidence）。校验失败 → LLM 修订一次 → 仍失败则 `Blocked / manual review`。

> 注意：validator 只能证明"引用存在"，**不能**证明"引用支持该结论"，后者由 §3 的 evidence support 评估负责。

---

## 3. 评估设计

### 3.1 对比的系统

| 系统 | 描述 |
|---|---|
| **B0 Baseline** | One-shot LLM，同一 schema，无工具，无前序 context |
| **S1 Workflow, no context** | 完整五阶段 + agent，但工具禁用 prior-meeting 访问（只能读当前会议） |
| **S2 Workflow + context** | 完整系统，可访问前序会议 |
| （可选）S3 Fixed pipeline | 固定顺序调用所有查询，不自适应；用于说明 adaptive 的价值（成本 vs 准确率） |

S1 vs S2 即老师要求的 **with / without prior-meeting context**；S2 vs S3 说明 adaptive 的意义。

### 3.2 指标

| 维度 | 指标 | 数据 / 判定方式 |
|---|---|---|
| 抽取 | Decision / Action P / R / F1 | 与 AMI gold 做语义匹配（embedding 阈值 + LLM judge，人工抽查 20%） |
| **Owner accuracy** | 在匹配成功的 action 上，owner 与人工标注一致的比例；另分别报告 explicit / ambiguous 子集 | Gold-Plus 子集 |
| **Deadline accuracy** | 同上，相对日期解析后比较；"无 deadline" 也是合法标签 | Gold-Plus 子集 |
| 歧义检测 | 对 ambiguous/missing 的 P/R；**错误自信率**（标为 verified 但实际错误） | Gold-Plus |
| **Evidence support** | 对每条 evidence：是否**真正支持**该 action 及其 owner/due。三档：supported / partial / unsupported | LLM judge（盲评，不知系统来源）+ 人工抽检 30 条，报告 judge–human 一致率 |
| Provenance validity | 引用 turn / prior_ref 存在率 | 代码；作为 evidence support 的对照，显示"存在 ≠ 支持"的差距 |
| 跨会议变更 | change_vs_prior 的分类准确率（new/update/reassign/deadline_change/cancel） | Gold-Plus `prior_link` |
| 结构有效性 | schema 通过率、一次通过率、Blocked 率 | 代码 |
| Agent 行为 | 平均调用数/item、工具使用分布、预算耗尽率、无效 action 率 | 决策日志 |
| 成本 | token、延迟 | 日志 |

**Evidence support 的 judge 协议**：输入 = (claim, quoted turns)，输出 = supported / partial / unsupported + 一句理由；judge 不看系统输出的其他字段，避免被置信度带偏。用同一批数据让两位成员人工评 30 条，报告一致率，以说明 judge 可信度。

### 3.3 消融与统计

- 主表：B0 / S1 / S2（/ S3）× 上述指标，仅在 Test 集上报告。
- 切片分析：只看 `DUE_CONFLICT`、`POSSIBLE_UPDATE`、`OWNER_AMBIGUOUS` 条目，预期 S2 在这些切片上明显优于 S1。
- 样本量小，报告原始计数（如 18/24）和 bootstrap 置信区间，不夸大结论。
- 失败分析：从日志中按类别人工归因（检索错误 / 判断错误 / 证据不足 / 标注歧义 / 预算耗尽）。

---

## 4. Demo 设计：展示跨会议变化

**选材**：同一 series 的相邻两场（如 X-a → X-b），其中至少有一个 action 的 owner 或 deadline 发生变化，或一个 decision 被修改。人工预先确认变化存在。

**展示顺序（一个页面/Notebook/终端脚本，四栏）**

1. **Earlier record**：会议 a 的结构化记录中的相关 action（owner、due、证据 turn）。
2. **Current meeting excerpt**：会议 b 中引发变化的几句话，高亮 "no, let's let X do it" 之类。
3. **Retrieved evidence + agent trace**：agent 的逐步决策，例如
   - Step 1 `flag=POSSIBLE_UPDATE`，调用 `get_prior_actions` → 命中 a/a2
   - Step 2 `read_turns` 读取 a2 的证据窗口，确认任务内容相同
   - Step 3 `get_participants` 确认新 owner 在场
   - Step 4 `finish(status=update/reassign)`
   同时并排展示 **S1（无 context）** 的结果：把它当成新任务或 owner 缺失。
4. **Resulting action table**：

| ID | Action | Owner | Due | Change vs prior | Evidence | Status |
|---|---|---|---|---|---|---|
| a3 | ... | UI (was ID) | next meeting | reassign ← a/a2 | b:turn_112; a:turn_77 | verified |

再补一个"agent 选择不同路径"的第二个 case：例如某 action 只是 owner 不明，agent **只**调用了 `get_participants` 就结束，说明它不是每次都走同一流程；以及一个预算耗尽后正确输出 `unresolved + 澄清问题` 的 case。

---

## 5. 与课程要求的对照

| 要求 | 对应设计 |
|---|---|
| 工具：parser + context lookup | transcript parser；`get_prior_actions / decisions / search_transcript / read_turns / get_participants` |
| 控制模式 | extraction → normalization → ambiguity check（Triage + agent）→ context verification（agent 工具调用）→ structured output（validator + artifacts） |
| 评估：抽取 | Decision / Action F1 |
| 评估：owner/date | Owner / Deadline accuracy（人工标注子集） |
| 评估：歧义检测 | 歧义 P/R、错误自信率 |
| 评估：provenance | 引用存在率 + **evidence support** |
| 评估：结构有效性 | schema 通过率、一次通过率 |

---

## 6. 建议里程碑

| 周 | 任务 |
|---|---|
| 1 | 确定 series 切分并冻结；写 parser；定义 schema 与 Gold-Plus 标注规范 |
| 2 | 实现 extraction / normalization / triage；完成 B0 baseline |
| 3 | 实现 context store 与工具、agent 循环、预算与日志；Dev 集迭代 |
| 4 | Test 集人工标注（owner/deadline/prior_link）；写评估脚本与 LLM judge |
| 5 | 在 Test 集跑 B0 / S1 / S2；失败分析；做 demo |
| 6 | 报告与展示 |

## 7. 风险与应对

- **标注量过大** → 严格限定到 4–6 场；两人分工 + 重叠计算 κ。
- **AMI 前后会议变化样本太少** → 选 series 时先筛查，必要时在报告中说明变更样本数，并把 context 消融限定在这个切片上。
- **LLM judge 偏差** → 盲评 + 人工抽检一致率。
- **Agent 乱调用/循环** → 代码硬限制 + 去重 + 白名单，统计无效 action 率。
- **信息泄漏** → 工具层强制时间顺序；gold 不入 context store；Test series 开发期不可见。
- **只证明"能跑"而不是"更好"** → 保留 B0 与 S3 对照，诚实报告 adaptive 没有收益的指标（如成本更高）。
