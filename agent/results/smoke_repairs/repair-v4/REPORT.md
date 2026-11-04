# 试跑问题修复：同输入开发回归

总体结果：**GAPS_REMAIN**。基于 `3b886f6`；本次修改尚未 commit。

与上轮相同的 IS1008a、TS3006a、TS3006b TXT、参与者元数据、scenario 文档、Portkey gpt-5-nano 和预算。新建独立空缓存，所有阶段真实运行；历史仅来自本轮更早记录。只有 P2/selection 策略版本号改变。

| 检查 | 结果 |
| --- | --- |
| all_pipeline_and_artifact_checks | False |
| slides_present | False |
| id_working_design_present | False |
| no_empty_revision_calls | True |
| published_cross_meeting_link | False |
| same_controlled_revision_passed | False |

| 会议 | 状态 | 秒 | 调用 | 已知 token | 可记录/输入 | 澄清问题 |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| IS1008a | COMPLETED | 396.1 | 33 | 288190 | 7/12 | 13 |
| TS3006a | FAILED | None | 13 | 117613 | —/— | — |
| TS3006b | NOT_RUN | — | — | — | —/— | — |

## 前后差异

- slides Action：0 → 0。
- ID working design Action：0 → 0。
- 自然运行有限修正空参数调用：1 → 0。
- 有来源证据的最终跨会议关系：0 → 0。

完整条目和引用见 summary.json；每一条关联的旧记录、双方原文和模型理由见 links.json。

## 证据与局限

- captured_cap_comparison.json 使用完全相同的旧候选池重放，可单独归因排序修复。完整试跑重新调用抽取、合并和投票，输出也包含模型变化，不能把全部内容差异都归因于本次代码。
- 有限修正受控诊断沿用上轮相同的真实证据和 Friday 故障输入，独立保存在 revision_probe/；不计作自然成功案例或 F1。
- 请求、可选工具及模型上下文见 requests.jsonl；阶段结果、原始 checkpoint、P2 检查、澄清和报告在 run/。
- 所有源码、模型、配置和输入哈希见 study.json 与 source/。上轮试跑、UI 和标注文档哈希保持原样。
- 本轮是开发回归，没有读取 gold、计算 F1、运行最终 evaluation，也未 commit。

## 错误

- TS3006a: FAILED
- TS3006b: missing result
