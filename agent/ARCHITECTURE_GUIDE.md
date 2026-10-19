# Architecture 逐步讲解（配合 ARCHITECTURE_V4.md 阅读）

V4 是规格表，这份是"说人话"的版本：先列出**所有工具和组件**，再解释**每一步在做什么、为什么需要、用到哪些工具**，最后用一个真实的 gold 例子走一遍。

---

## A. 先看全景：所有要实现的东西

### A1. 代码工具（确定性，不用模型）

| 编号 | 名称 | 做什么 | 输入 → 输出 | 谁调用 |
|---|---|---|---|---|
| C1 | `transcript_parser` | 把 TXT 转成 JSON（仓库已有） | TXT → 9 字段 dialogue-act 数组 | N1 |
| C2 | `participant_enricher` | 给每条 dialogue act 补参与者 ID 和角色 | JSON + 参与者表 → 带 `attributes.participant/role` 的 JSON | N1b |
| C3 | `normalizer` | 把"I / the designer / we"映射成参与者 ID，deadline 分类，去重 | 候选条目 → 规范化条目 | N3 |
| C4 | `triage` | 给每个条目打 flag，决定是否进入 agent | 规范化条目 → flags | N4 |
| C5 | `validator` | 按 schema 和规则检查输出 | 记录 → 通过 / 失败原因列表 | N8 |
| C6 | `renderer` | 生成可读报告与 JSON，写入前序记录库 | 记录 → 文件 | N10 |
| C7 | `budget_guard` | 强制调用次数上限、白名单、禁止重复调用、禁止读未来会议 | agent 动作 → 放行/拒绝 | N5 |
| C8 | `trace_logger` | 记录每次决策与工具调用 | 事件 → `trace.jsonl` | 全程 |

### A2. 模型调用（LLM）

| 编号 | 名称 | 做什么 | 使用的字段模板 |
|---|---|---|---|
| L1 | `extractor` | 从转写中抽出 abstract / actions / decisions / problems 候选，附证据 | 类型定义 + 证据要求 |
| L2 | `follow_up_agent` | 对有疑问的条目，自己选工具去查，最后给出结论 | basis 含义、deadline/status 规则、三者区别 |
| L3 | `assembler` | 写 abstract 和报告概述（不新增条目） | 完整字段表 |
| L4 | `reviser` | 校验失败时只修指出的字段 | 完整字段表 + 失败原因 |

### A3. Agent 可调用的检索工具（retrieval，只读）

| 编号 | 工具 | 查什么 | 返回 | 产生的 `reference.source_type` |
|---|---|---|---|---|
| R1 | `search_dialogue(query, scope, k)` | 在当前或**更早**会议的转写里搜相关发言 | `[{da_id, meeting_id, speaker, text, score}]` | `dialogue` |
| R2 | `read_dialogue(meeting_id, da_id, window)` | 读某条发言前后的对话 | 邻近 dialogue acts | `dialogue` |
| R3 | `get_participants(meeting_id)` | 查某场会议谁是谁、什么角色 | `[{speaker, participant_id, role}]` | `participant_metadata` |
| R4 | `get_prior_items(type, topic, k)` | 查前序会议**系统自己产出**的 action/decision/problem | 条目：text、owner、deadline、status、证据 | 用于 `related_items` |
| R5 | `search_project_doc(query, k)` | 查 AMI 项目说明（`amiScenario.pdf`） | `[{page, section, snippet}]` | `project_document` |
| R6 | `finish(...)` | 提交该条目的最终字段（不是检索，但是 agent 的"结束"动作） | — | — |

### A4. 数据库（Context Store）

| 库 | 内容 | 谁用 |
|---|---|---|
| T 转写库 | 当前及同 series 更早会议的转写（已补参与者） | R1、R2 |
| P 参与者表 | 每场会议 speaker 字母 → 参与者 ID → 角色（来自 `dialogueActs` 的 `attributes`） | C2、R3 |
| S 项目文档库 | `amiScenario.pdf` 的文本切块 | R5 |
| R 前序系统输出库 | 前面会议跑完后产出的记录 | R4 |

**不进入任何库**：gold、summlink、abstractive 标注、同 series 之后的会议、其他 series 的会议。

---

## B. 每一步在做什么

### N0 读取输入
- **意思**：用户给一个 `.txt`，先弄清楚这是哪场会议。
- **做什么**：读文件头 `# Meeting: TS3006b`，得到 series=TS3006，序号=2（b）。这个序号决定后面"能读哪些前序会议"：只能读序号更小的。
- **产出**：`meeting_id, series_id, order`。
- **失败**：读不到合法会议 ID → 停止，标 BLOCKED。

### N1 解析（C1）
- **意思**：模型和检索都更适合处理结构化数据，所以把 TXT 变成 JSON。
- **做什么**：每行 `[34.27 --> 37.14] A (ES2011a.A.dialog-act.dharshi.1): Here we go .` 变成一条记录，保留 id、speaker、时间、text；其余字段（词 ID、label）为 null，`attributes` 为空。
- **为什么重要**：这个 `id` 将来就是证据引用的"定位符"。
- **失败**：格式错误、重复 ID、时间戳非法 → 报错并指出行号，停止。

### N1b 补参与者（C2）
- **意思**：转写里只有 A/B/C/D，但 A 在这场会议是 PM，在另一场可能是 UI。owner 要写成稳定的参与者 ID（如 `MTD021PM`），所以必须先建立对应关系。
- **做什么**：用 P 表把每个 speaker 字母换成 (participant, role) 写进 `attributes`。同时把这场会议写入 T 转写库，供后面检索。
- **失败**：某个字母对应不上唯一的人 → 停止。

### N2 抽取（L1）
- **意思**：先宽松地找出"可能的"决策、action、问题。
- **做什么**：把转写分块（每块约 150 条，重叠 20 条）交给模型，要求每个候选都带：条目文字、类型、**证据 dialogue-act ID**、对话中关于 owner 和 deadline 的**原话线索**（不推断）。
- **产出**：候选列表。
- **过滤**：没有真实证据 ID 的候选丢掉（记日志）。
- **失败**：输出格式不合法 → 重试一次，仍不行则停止。

### N3 规范化（C3）
- **意思**：把口语变成可比较的值。
- **做什么**：
  - "I'll do it" → 说这句话的人的参与者 ID；
  - "the designer" → 本场会议里 ID 角色的那个人；
  - "we / someone / everyone" → 标 `ambiguous`，**不扩展成所有人**；
  - deadline 保留原话（"by next meeting"），只判断有没有，不换算成日期；
  - 合并分块重叠造成的重复条目。
- **产出**：带初步 owner、deadline 和 basis 的条目。

### N4 分诊（C4）——决定是否需要 agent
- **意思**：不是每个条目都需要去查资料。明确的直接过，有疑问的才走 agent。
- **规则**（示例）：
  - owner 空或不确定 → `OWNER_UNRESOLVED`
  - 像是多人共同负责 → `JOINT_OWNER_CHECK`
  - 和前序会议里的某个条目很像 → `POSSIBLE_PRIOR`
  - 措辞像完成/取消/改为 → `STATUS_CHECK`
  - 证据太弱 → `WEAK_EVIDENCE`
  - 涉及角色职责 → `SCENARIO_CHECK`
- **去向**：没有 flag → 直接 N7；有 flag → N5。
- **意义**：这样 agent 只处理难的条目，节省成本，也体现"不同条目走不同路径"。

### N5 跟进 agent（L2 + R1–R6，受 C7 限制）
- **意思**：这是"自适应"的部分。agent 看着一个有疑问的条目，**自己决定下一步查什么**，查到了再决定是继续查还是结束。
- **每一步**：模型输出一个动作（调用哪个工具、参数是什么、为什么）→ C7 检查是否允许 → 执行 → 把结果放回 agent 的状态里 → 下一步。
- **选择例子**：
  - 只是不知道 B 是谁 → `get_participants` → `finish`（1 步）
  - 不确定是不是前一场任务的延续 → `get_prior_items` → `read_dialogue`（读前序证据）→ `finish`
  - 前序没找到 → 换关键词 `search_dialogue(scope=prior)` 一次 → 还没有就 `finish`（无关联）
- **停止条件**（由代码强制）：agent 调用 `finish`；单条目 4 次调用用完；整场会议 30 次用完；连续两步没有新信息；连续两次非法动作。
- **不能追问用户**：查不出来就如实标 `ambiguous / missing`，owner 填 null，放入报告的"待确认事项"。
- **产出**：该条目的最终字段：owner、owner_basis、deadline、deadline_basis、status、prior_context_needed、related_items、reference。

### N6 检索工具执行（R1–R6）
- **意思**：N5 里 agent 想调用的工具在这里真正执行。
- **做什么**：从 T/P/S/R 库查询，返回结果。每次返回都带一个 `new_info`（这次有没有带来新信息），用于判断是否陷入空转。
- **保证**：只能读同 series、序号更小的会议；返回的每个 ID/页码都真实存在。

### N7 拼装（C + L3）
- **意思**：把所有条目整理成最终记录。
- **做什么**：代码按字段模板拼装 actions / decisions / problems；每个条目 ID 形如 `TS3006b.sys.s.12`；模型只写 abstract 和报告开头的概述，**不能新增条目**。

### N8 校验（C5）
- **意思**：机器检查输出有没有违反规则。
- **检查**：符合 schema；每类字段数对（abstract 4、decisions/problems 7、actions 11）；basis 和值一致（explicit 就必须有 owner，ambiguous 就必须是 null）；引用的 ID 都存在；`related_items` 只指向更早的条目；没有 `unreviewed`。
- **注意**：这只能证明"引用存在、格式对"，**不能证明证据确实支持结论**；后者靠评估阶段的 evidence support 检查。

### N9 修订一次（L4）
- **意思**：给一次改错的机会。
- **做什么**：只修 N8 指出的字段，不加不删条目，不调用工具；改完重新校验；仍失败就停止并标 BLOCKED，保留草稿和错误列表供人工处理。

### N10 输出（C6）
- **可读报告**：概要、决策表、问题表、action 表、与前序会议的变化、待确认事项、证据原文附录。
- **JSON**：与 gold 同构，用于评估。
- **trace**：每个决策和工具调用的日志。
- **写入 R 库**：本场结果供下一场会议使用。

---

## C. 用真实 gold 条目走一遍（TS3006a → TS3006b）

gold 里有这样一对（来自 `revised_gold_output`）：

| 会议 | 条目 | 内容 | owner | 关系 |
|---|---|---|---|---|
| TS3006a | `TS3006a.elana.s.9` | UI 设计师将指定遥控器的技术功能 | MTD023UID（explicit） | — |
| TS3006b | `TS3006b.dharshi.s.12` | UI 设计师将开发遥控器布局和 LCD 菜单（含导航、按键用法） | MTD023UID（explicit） | **updates** → `TS3006a.elana.s.9`；`prior_context_needed=true` |

**系统怎么处理 b（示意，不是真实运行结果）**

1. 上传 `TS3006b.txt`；N0 得到 series=TS3006，序号=2。
2. N1 转成 JSON；N1b 补上 B→`MTD023UID`（UI）等。
3. N2 从发言里抽出"UI 要做遥控器布局和 LCD 菜单"这条候选，附证据 dialogue-act ID；owner 线索是该角色名。
4. N3 把角色词 "user interface designer" 映射为 `MTD023UID`。
5. N4：这条和前一场的 UI 任务相似 → 打上 `POSSIBLE_PRIOR`。
6. N5：agent 调用 `get_prior_items`（读 a 的系统输出）→ 命中 a 里的 UI 技术功能任务 → 必要时 `read_dialogue` 看 a 里的原始对话确认 → `finish`，给出 `related_items = [{a 里对应条目, updates}]`，`prior_context_needed = true`，并写上 reference。
7. N7/N8/N10：拼装、校验（`related_items` 指向更早的条目 → 通过）、输出报告，"与前序会议的变化"栏显示：a 的"指定技术功能" → b 的"开发布局和 LCD 菜单"。

**对比无前序上下文的系统（S1）**：它在第 6 步查不到前序，只能把这条当成新任务，`related_items=[]`、`prior_context_needed=false`——这正是我们要展示的差异。

---

## D. 为什么这样分工

| 做法 | 原因 |
|---|---|
| 解析、规范化、校验用代码 | 确定性强，可重复，出错可定位 |
| 抽取用模型 | 从口语对话中找条目需要语言理解 |
| 只对有疑问的条目启用 agent | 控制成本；体现自适应（不同条目走不同路径） |
| 工具权限和次数上限放在代码里 | 防止 agent 乱读、读到未来会议或无限循环；符合老师"限制和日志在代码里"的要求 |
| 字段模板写进 prompt 并用同一份 schema 校验 | 模型知道要填什么，机器能验证填对了没有 |
| gold 不进运行时 | 防止泄漏，保证评估可信 |
