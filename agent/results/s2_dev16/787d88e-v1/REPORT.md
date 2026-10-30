# 十六场开发会议：空缓存完整 S2 回归

固定版本：`787d88e34d803a3bcafa4817720634f2dd74b50e`；分支 agent-development。未 commit，未修改生产代码、UI 或标注文档。

范围：ES2011、IS1008、TS3004、TS3006，各 a→d。原生 pipeline 从 TXT 真实运行；新目录、新缓存、未复制旧记录、未扣历史预算。模型、提示词、配置、整场预算和原生重试保持不变。未读取 gold 或留出集转写。

完成 **13/16（81.25%）**，实际尝试 14 场；产物审计 **PASSED**。失败同系列的后续会议按原生串行策略阻塞，其他系列继续；不替换历史、不重跑择优。

| 会议 | 状态 | 秒 | 模型尝试 | 计入 token | 可记录/P2 输入 | 澄清 | 发布关联 |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| ES2011a | COMPLETED | 325.5 | 29 | 208261 | 8/9 | 5 | 0 |
| ES2011b | COMPLETED | 482.1 | 37 | 343205 | 12/14 | 10 | 0 |
| ES2011c | COMPLETED | 593.5 | 54 | 486966 | 8/10 | 9 | 0 |
| ES2011d | COMPLETED | 708.5 | 78 | 684418 | 8/13 | 22 | 1 |
| IS1008a | COMPLETED | 309.7 | 41 | 380440 | 8/10 | 6 | 0 |
| IS1008b | COMPLETED | 289.8 | 48 | 427407 | 7/10 | 15 | 0 |
| IS1008c | COMPLETED | 475.2 | 74 | 652609 | 12/16 | 22 | 1 |
| IS1008d | COMPLETED | 475.0 | 68 | 591660 | 12/14 | 16 | 2 |
| TS3004a | COMPLETED | 202.4 | 37 | 291565 | 7/9 | 8 | 0 |
| TS3004b | COMPLETED | 464.8 | 45 | 429976 | 6/8 | 11 | 0 |
| TS3004c | COMPLETED | 673.4 | 53 | 556622 | 9/12 | 11 | 0 |
| TS3004d | COMPLETED | 607.6 | 64 | 600148 | 13/13 | 8 | 3 |
| TS3006a | COMPLETED | 323.6 | 46 | 378138 | 13/17 | 19 | 0 |
| TS3006b | FAILED | 357.407 | 20 | 309656 | —/— | — | — |
| TS3006c | BLOCKED_BY_PRIOR_FAILURE | — | 0 | — | —/— | — | — |
| TS3006d | BLOCKED_BY_PRIOR_FAILURE | — | 0 | — | —/— | — | — |

总模型尝试 694；已知 token 6,256,872，计入 token 6,341,071（未知用量保守计入 84,199）。计入 token 不等于供应商账单。

## 真实调用阶段

| 会议 | 抽取 | 合并 | Decision 重合并 | 选择 | P2 | 自主工具决策 | 摘要 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ES2011a | 6 | 5 | 4 | 1 | 10 | 2 | 1 |
| ES2011b | 7 | 5 | 3 | 1 | 18 | 2 | 1 |
| ES2011c | 8 | 5 | 4 | 1 | 17 | 18 | 1 |
| ES2011d | 9 | 4 | 6 | 1 | 33 | 24 | 1 |
| IS1008a | 4 | 4 | 3 | 1 | 22 | 6 | 1 |
| IS1008b | 7 | 3 | 4 | 1 | 19 | 13 | 1 |
| IS1008c | 7 | 4 | 3 | 1 | 32 | 26 | 1 |
| IS1008d | 7 | 3 | 5 | 1 | 27 | 24 | 1 |
| TS3004a | 8 | 3 | 3 | 1 | 18 | 3 | 1 |
| TS3004b | 12 | 4 | 5 | 1 | 14 | 8 | 1 |
| TS3004c | 13 | 6 | 6 | 1 | 18 | 8 | 1 |
| TS3004d | 12 | 5 | 5 | 1 | 18 | 22 | 1 |
| TS3006a | 8 | 3 | 3 | 1 | 27 | 3 | 1 |
| TS3006b | 12 | 3 | 3 | 2 | 0 | 0 | 0 |
| TS3006c | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| TS3006d | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## 检查与限制

- 完成场次逐个校验原生 JSON/schema、P2 与澄清映射、报告问题覆盖、文件哈希、阶段/Agent trace、TXT→解析 checkpoint 和调用/token 账本。
- 每场 80 次调用、100 万 token、请求超时 300 秒、整场超时 1200 秒、并发 4、重试 2；每个系列后续会议只读取本轮已发布的同系列更早记录及原文。
- stages 和 runtime_control 是实际尝试与重试的依据；requests.jsonl 记录逻辑请求，不应当成供应商调用次数。
- 关联 witness 校验旧记录、双方原文及最终对应关系；来源验证通过不等于关系语义已人工确认。本轮不计算 gold P/R/F1，不作为最终 evaluation 或稳定性保证。
- 发布关联 7 条；逐条旧记录→工具返回→Agent 判断→新记录见 [可读关联清单](CROSS_MEETING.md) / cross_meeting_links.json，未决关系见 pending_relations.json。
- P2 结果：155 条检查条目，accepted 65、pending 90；可记录 123 条，其他未发布条目不能当作已完成内容验证。澄清请求 162 条。
- 自然运行有限修正 0 次；事件及重新检查见 limited_revisions.json。没有额外错误注入或单条重复。

## 失败与审计问题

- TS3006b：FAILED；阶段 select；原因 stage_failed；已有 checkpoint 和 partial 见该场 result.json。

### TS3006b 选择提交失败：零 API 原响应复现

两次真实选择提交都在明确标为重复、keep=false 的 C-key 中遗漏必需 reason，首先触发 C10 校验错误。原参数交给冻结 canonical_reviews 后，两次错误完全复现；没有重新采样、补字段或修复生产代码。
失败时只用了 20/80 次调用、309,656/1,000,000 token；P2/摘要尚未进入。该次失败不是 P2 拦截或预算耗尽。parsed、extracted、consolidated checkpoint、两次原始选择响应、trace 和 partial 均保留。
TS3006c/d 因缺少本轮完整 b 历史而受阻，实际 API 调用为 0；不能算作已跑过。具体字段、原响应序号和路径见 selection_failure.json。

## 原始输入警告

- ES2011c：TranscriptTimingWarning；ES2011c: line 10: endtime precedes starttime; preserved unchanged

输入警告按原 parser 保留，没有改写原始时间或转写。

## 来源内容抽查

- TS3006a_independent_economic_decisions：POSITIVE_SOURCE_CHECK。The cold pipeline separately published 'Pricing set at 25 Euros.', 'Sales target: 50 million Euros in total.', and 'Production cost: half of the selling price.'. These were not collapsed into one output Decision. This records preservation of the known independent facts, not a new F1 score or independent human sign-off.
- IS1008d_market_tests_vs_marketing：NEEDS_SEMANTIC_REVIEW。The model treats adding prototype market tests as a scope update to an earlier marketing task. The quotations support both activities, but they may be distinct tasks in the same marketing plan. They do not explicitly establish that the earlier commitment is being updated. Keep the model output unchanged and independently review relationship identity and changed scope.
- IS1008a_slides_retained_in_cold_run：POSITIVE_SOURCE_CHECK。The cold pipeline retained the slides Action and an explicit meeting-end deadline. P2 item IS1008a#10 finished with all_required_checks_passed. Evidence references include the request in IS1008a.C.dialog-act.s9553330.42 and PM's agreement in IS1008a.A.dialog-act.s9553330.130/131. This is a natural full-run source check, not an injected case or a replay of old selection results.
- ES2011d_fruit_scope_link：NEEDS_SEMANTIC_REVIEW。The older source follows a 'Maybe we should design couches' suggestion and vocal reactions. Treating it as an adopted project scope may overstate the discussion. The newer source supports fruit variants for the remote but does not clearly establish that it updates the prior couch/remote scope. Actual source citations pass structural checks, while the old decision and relationship semantics need independent review.

这些是 Codex 的开发来源抽查，不是新增人工确认；没有修改模型输出或评分，完整引用见 source_reviews.json。

## 复查入口

- study.json / source/ / inputs/：固定代码、模型、配置、输入及哈希。
- run/s2/<meeting>.json、*_report.md、*.p2.json、*.clarifications.json：主记录与待确认项。
- run/s2/*.stages.jsonl、*.trace.jsonl、_attempts/：所有阶段、实际模型响应、工具返回及原始 checkpoint。
- summary.json：每场状态、预算、调用分布、P2 状态、工具与异常；manifest.json / verification.json：最终完整性。
