# 试跑问题修复：同输入开发回归

总体结果：**PASSED**。基于 `3b886f6`；本次修改尚未 commit。

与上轮相同的 IS1008a、TS3006a、TS3006b TXT、参与者元数据、scenario 文档、Portkey gpt-5-nano 和预算。新建独立空缓存，所有阶段真实运行；历史仅来自本轮更早记录。只有 P2/selection 策略版本号改变。

| 检查 | 结果 |
| --- | --- |
| all_pipeline_and_artifact_checks | True |
| slides_present | True |
| id_working_design_present | True |
| no_empty_revision_calls | True |
| published_cross_meeting_link | True |
| same_controlled_revision_passed | True |

| 会议 | 状态 | 秒 | 调用 | 已知 token | 可记录/输入 | 澄清问题 |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| IS1008a | COMPLETED | 359.0 | 26 | 217135 | 9/10 | 3 |
| TS3006a | COMPLETED | 610.8 | 33 | 319721 | 7/9 | 12 |
| TS3006b | COMPLETED | 797.3 | 74 | 664205 | 12/19 | 20 |

## 调用与时间对比

| 主试跑 | 调用 | API 已知 token | 秒 |
| --- | ---: | ---: | ---: |
| 修复前 | 145 | 1238649 | 1072.3 |
| 本次 | 133 | 1201061 | 1767.1 |

本轮耗时更长，不能宣称速度改善。上表不混入中间失败实验或独立修正诊断；单次真实模型运行有输出和服务时延变化，没有进行显著性或稳定性评估。

## 前后差异

- slides Action：0 → 1。
- ID working design Action：0 → 1。
- 自然运行有限修正空参数调用：1 → 0。
- 有来源证据的最终跨会议关系：0 → 1。

完整条目和引用见 summary.json；每一条关联的旧记录、双方原文和模型理由见 links.json。

## 实际发布的跨会议链路

以下为模型输出及 Codex 原文核查材料，未新增人工确认，也未把旧的 TV 范围更新标签移用到新案例。

旧记录 `TS3006a.sys.s.4`：Specify the technical functions.

实际检索/读取：`[{"tool": "get_prior_items", "args": {"query": "group two groups display basic UI", "item_type": "actions"}}, {"tool": "get_prior_items", "args": {"query": "group two groups display basic UI functions", "item_type": "actions"}}, {"tool": "read_prior_item", "args": {"item_id": "TS3006a.sys.s.4"}}]`。

Agent 选择：`continues`；理由：Continuation of feasibility/structure from earlier 'specify the technical functions' item to concrete grouping task.

新记录 `TS3006b.sys.s.9`：The UI Designer will group the functions into two groups: display functions and basic functions.

旧证据：
- `TS3006a.A.dialog-act.vkaraisk.149` Uh the User Interface Designer should specify the technical functions .
- `TS3006a.A.dialog-act.vkaraisk.150` Right ? Yeah ?
- `TS3006a.B.dialog-act.vkaraisk.59` Yep .

当前证据：
- `TS3006b.B.dialog-act.dharshi.169` Um the <disfmarker> one group for the for the display , and one group for the basic functions ,
- `TS3006b.B.dialog-act.dharshi.168` Uh I think we have to to group , to make two groups .

当前记录状态为 `pending`；未确认字段：`deadline`。旧 JSON 保持不变。

## 证据与局限

- captured_cap_comparison.json 使用完全相同的旧候选池重放，可单独归因排序修复。完整试跑重新调用抽取、合并和投票，输出也包含模型变化，不能把全部内容差异都归因于本次代码。
- 有限修正受控诊断沿用上轮相同的真实证据和 Friday 故障输入，独立保存在 revision_probe/；不计作自然成功案例或 F1。
- 请求、可选工具及模型上下文见 requests.jsonl；阶段结果、原始 checkpoint、P2 检查、澄清和报告在 run/。
- 所有源码、模型、配置和输入哈希见 study.json 与 source/。上轮试跑、UI 和标注文档哈希保持原样。
- 本轮是开发回归，没有读取 gold、计算 F1、运行最终 evaluation，也未 commit。
