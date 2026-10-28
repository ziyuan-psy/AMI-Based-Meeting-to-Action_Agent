# P2 检查、动作闭环与澄清请求

S1/S2 默认启用 P2，B0 保持无跟进基线。设置 `MTA_P2_ENABLED=0` 可运行旧 Agent 协议作为消融。
输出记录仍使用原来的 0.5.0 schema；P2 结论和问题放在独立 sidecar，UI 接口未改动。

## 检查如何驱动动作

P2 检查每个条目，包括原来没有 triage flag 的条目。模型通过 `assess_item` 提交拟输出字段和适用检查。
正式 sidecar 始终保留六项；非 Action 的 owner/deadline 由代码填为不适用，无需模型重复提交：

| 检查 | 要回答的问题 |
| --- | --- |
| statement | 原文是否支持这一条实质任务／决策／问题及其范围，而不是议程、泛泛流程或不成立的陈述？ |
| owner | 当前会议是否支持被指派的人？发言者和名册不能单独证明责任人。 |
| deadline | 当前会议是否支持这一截止时间及原始措辞？未确定时保留 null。 |
| status | 是否支持会议结束时的状态，包括条件和不确定性？ |
| conflict | 是否存在未消除的矛盾，影响 statement、owner、deadline、status 或 related_items 哪个字段？ |
| relation | 是否检索到相关历史记录，并实际读到了它的支持证据和本次变化证据？ |

每项检查给出 `pass / fail / uncertain / not_applicable`、简短原因和已读对话 ID。
代码将 ID 绑定为原文引文；兼容旧的 `{da_id, quote}` 输入，但不会替模型修正伪造引文。
受影响字段仅 conflict 需要选择，其他检查的字段身份由代码按检查名确定。
owner/deadline 对非 Action 不适用；其余必要检查不能用 not_applicable 跳过。
`next_action` 是可选建议；缺少建议时，控制器仍从完整检查结果推导安全动作，不能据此跳过检查。

控制器重新校验这些结果，再选择动作：

| 条件 | 控制器动作与后果 |
| --- | --- |
| 所有必要检查通过 | `accept`，返回已检查字段。 |
| 有可查证的证据缺口 | `retrieve`，允许模型选择查找、读上下文、读历史记录或名册等工具。 |
| 搜索无帮助或模型建议重新查询 | `search_again`，仅允许搜索工具；模型调整关键词或 scope。 |
| 歧义／冲突无法确定，或查证额度耗尽 | `request_clarification`，终止条目处理并输出具体问题。 |
| 模型判断继续处理无益，或达到无进展／无效动作／步数上限 | `stop`，保留待确认结论，不能退回初始猜测再当成已接受。 |

一次工具返回后必须再检查，不能直接调用旧 `finish` 绕过检查。
例如，模型建议 accept，但 owner 仍 uncertain，代码会改为 retrieve 或待确认；
检索为空后可以改查询再次搜索；检索到历史条目后可以继续 read_dialogue，再检查关联。
查询文本、工具选择及何时主动请求澄清仍由 Agent 决定，代码负责允许范围、门槛和停止条件。
非 Action 若因不适用的 owner/deadline 提议停止，最多先反馈一次适用性纠正，再由 Agent 重新选择；
它仍可以保留实际未解决字段并停止。这次重审也计入统一模型预算和回合上限。

跨会议核验可用 `read_prior_item` 读取旧条目及其原文，随后用 `assess_relation` 聚焦比较。
具体边界、证据绑定和真实案例见[跨会议关联](CROSS_MEETING.md)与[人工核对案例](../results/cross_meeting/CASE.md)。

重复关键词（包括大小写和顺序变化）会复用结果、计为无进展，不能无限重查。
新增对话 ID 也不必然代表检查有进展：连续重复的未解决结论会触发停止。
格式错误反馈会指出具体字段；有效步骤后重置连续错误计数，两次连续错误终止，仍受总回合上限约束。
工具返回保持完整 JSON；超出 6,000 字符时只保留完整结果行，并注明省略数量。
只有模型实际收到的行才进入可引用证据集合。

## 硬约束和能力边界

- pass 必须有实际读过的原文支持。模型选择 ID，代码保存原文；旧格式引文必须逐字匹配。
  未读 ID、伪造／空白引文不能通过。无矛盾的 pass 可绑定已核验 statement 引文作为检查范围，
  不把某一引文当作不存在任何矛盾的证明；fail/uncertain 不会因此被改成 pass。
- 当前条目需要当前会议的支持；只引用上次会议的指派不能证明这次的责任、状态或新承诺。
- 声称历史关联时，需要实际检索到的记录 ID、当前变化引文，以及该历史条目的支持原文。
  只有历史检索命中或任意一条历史引文不够。
- 历史读取继续由 Store 限制为同系列、更早会议；S1 和系列第一场不提供历史条目工具。
- 所有检查与工具选择模型请求都经过第三步的统一 admission。默认仍是每场 80 次模型尝试、
  100 万 token、请求 300 秒、整场 1,200 秒；每条最多 4 次 lookup，整场最多 30 次。
  每条 12 个模型回合也包括检查回合，新增阶段没有独立免费额度。

语义支持、类型有效性和矛盾的含义仍由模型判断；代码硬校验出处、字段、状态和动作限制。
精确引文不等于数学证明其支持结论，pass 也不等于人工确认。本步没有声称提高了 extraction precision，
没有运行最终 evaluation，最终正确率仍需负责评估的组员独立核查。

## accepted、pending 与正式记录

accepted 表示所有必要检查通过。pending 表示仍有待解决字段，不能自动当成 accepted。

- 陈述本身有支持，只是 owner/deadline 未确定：保留任务，相关值清为 null，basis 为 missing 或 ambiguous，
  并输出字段问题。状态不确定时保留 unknown；未验证的关联不进入 related_items。
- 陈述本身未通过、类型／实质性存疑或存在影响陈述的冲突：保存在 P2 待确认产物中，
  `recordable=false`、`record_id=null`，不进入正式 JSON。
- 摘要在条目检查后生成，只接收可记录条目，避免被隔离的陈述重新从摘要流入结果。

“整场 COMPLETED”只代表运行和保存完成，不代表每个条目 accepted；要查看 P2 accepted/pending 计数。
技术超时、API 异常、总预算耗尽仍使用第三步的整场失败状态，保留 partial，不能伪装为语义检查通过。

## 输出契约

每次成功完成的运行写出：

- `<meeting>.json`、`<meeting>_report.md`：原 schema 的记录和可读报告。
- `<meeting>.p2.json`：run_id、检查开关、每条的 item_key、最终 record_id、六项检查、
  实际控制器动作历史、lookup 数、终止原因、可记录状态和待确认问题。
- `<meeting>.clarifications.json`：同一 run_id 的问题索引。

问题结构示例（示意值，不是真实标注）：

```json
{
  "request_id": "run-and-item-specific-id",
  "item_key": "TS3006b#2",
  "record_id": "TS3006b.sys.s.2",
  "field": "owner",
  "question": "Who is responsible for this task?",
  "item_text": "Develop the menu",
  "reason": "Two assigned roles disagree",
  "evidence_da_ids": ["TS3006b.A.dialog-act.example.1"],
  "available_evidence_da_ids": ["TS3006b.A.dialog-act.example.1"],
  "status": "open",
  "termination_reason": "unresolved_checks"
}
```

`item_key` 对应本次合并后的原始条目序号；剔除条目后不会把它误映射到另一个 record。
正式 ID 仅在组装时绑定。问题 ID 包含 run_id、item_key 与字段，避免重跑后误认旧问题。
`evidence_da_ids` 是该问题的引文依据；`available_evidence_da_ids` 是已读、可供人工复查的上下文，
不把“没有期限”假装成某一行能独立证明的事实。

`schemas/p2_output_schema.json` 校验两个 sidecar 的结构；额外检查 run、问题、记录 ID 的一致性，
已接受项没有未解决字段，正式字段与检查字段一致，没有未检查条目混入正式记录。
报告的 Needs human confirmation 区域会显示这些问题，并标明被隔离的草稿。

这是问题输出协议，不包含向他人发送消息、接收回答或恢复执行；这些可以作为后续独立功能。

## 保存与恢复边界

每轮检查和工具返回都会更新 `_attempts/<meeting>/<run_id>/checkpoints/p2_item_<n>.json`。
Trace 保存检查结论、模型建议动作、实际动作、实际交付的工具结果与停止原因，不保存隐藏推理过程。

成功时记录、报告及两个 sidecar 一起发布；任一文件发布失败都尝试恢复旧的四个文件。
批量脚本仅跳过具有当前 P2 开关状态、且记录／报告／P2／问题文件哈希都匹配的完成结果。
旧版没有检查的结果不会在 P2 开启时直接被跳过。

失败时 `_attempts/.../p2.partial.json` 与 `clarifications.partial.json` 保存已有检查和已形成的问题；
尚在执行的检查标为 interrupted，技术失败不会自动捏造一个语义问题。迟到响应不能继续改写产物。
这些 partial 是诊断材料，不会自动恢复执行或成为正式历史记录。

## 验证

`tests/test_p2.py` 使用明确的脚本响应验证接受、补查、换查询、冲突澄清、未知字段清空、
历史证据门槛、重复搜索、未读／伪造引文、整场预算、超时保存、四文件回滚和摘要隔离。
旧协议测试显式关闭 P2，保留旧行为消融；其他可靠性测试使用新版检查协议。

`scripts/p2_smoke.py` 可对已保存的 TS3006a 一个 Action、一个 Decision 做小型真实协议冒烟。
它只验证新协议和终止路径，不读取 gold、不算质量指标；输出目录必须新建，请求仍受统一预算约束。

本次保存了三个真实版本的协议记录：`results/p2_smoke/p2-v1/` 暴露不适用状态和格式纠错问题；
`p2-v2/` 中 Decision 通过检查而接受；`p2-v3/` 只复测 Action，在期限持续未解决时因无进展停止，
保留支持的任务和责任人、清空期限，并输出 deadline 问题。三个版本分别使用 13、8、5 次模型请求，
已知输入＋输出用量分别为 67,347、36,571、22,106 token。

最终代码通过测试离线回放 v2 的 Decision 和 v3 的 Action 真实响应，检查字段与结果一致。
这说明实际响应可以驱动接受和待确认路径，不代表语义判断已经经过独立人工确认，也不是整场性能比较。
