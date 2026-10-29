# 冻结版空缓存完整试跑

固定提交：`3b886f6c0c1520a1c5bc8628da7198a871c25495`。原冻结入口直接运行，模型和配置保持原样。

本次从 TXT 开始；没有复制任何模型响应缓存、归一化候选或旧历史记录。每场完整 80 次/100 万 token/1,200 秒预算，不扣历史预留。后续会议只读取本轮实际生成的更早记录。

运行与产物结构检查：**PASSED**。交付验收：**NOT_SIGNED_OFF**；本次未读取 gold 打分，不属于最终 evaluation。

| 会议 | 状态 | 秒 | 实际尝试 | 已知 token | 计入 token | 可记录/输入 | 待确认问题 |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |
| IS1008a | COMPLETED | 329.8 | 30 | 264703 | 264703 | 8/8 | 9 |
| TS3006a | COMPLETED | 305.5 | 50 | 361546 | 361546 | 12/15 | 15 |
| TS3006b | COMPLETED | 437.0 | 65 | 612400 | 612400 | 9/14 | 25 |

已知 token 是 API 返回的 input+output；计入 token 还可能包含失败请求的未知用量保守预留，不能当成供应商账单。

## 各阶段真实调用

| 会议 | 抽取 | 合并 | Decision 重合并 | 选择 | P2 检查 | 自主工具决策 | 摘要 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| IS1008a | 4 | 5 | 3 | 1 | 12 | 4 | 1 |
| TS3006a | 8 | 4 | 3 | 1 | 25 | 8 | 1 |
| TS3006b | 12 | 3 | 4 | 1 | 32 | 12 | 1 |

## P2 有限修正

状态：**ATTEMPTED_WITHOUT_VERIFIED_PUBLICATION**。有效修正 0 次，重新检查后发布 0 次。

“没有出现成功修正”不能用流程完成或单元测试通过替代。自然数据未触发时，只能说本次未覆盖该路径；被拒绝的参数/引用只证明护栏拦截，不能计作自主修正成功。
每个实际案例的原句、参数、证据、改后文本及后续检查见 [limited_revision_cases.json](limited_revision_cases.json)。

## 独立有限修正诊断

结果：**REPAIRED_AND_RECHECKED**。它与上面的自然完整流程分别保存；不计入自然案例、F1 或最终主记录。

对真实 slides 证据注入无依据的 `by Friday` 后，使用相同冻结源码、模型、配置和预算，Agent 自主选择工具。实际 3 次调用，16615 token。

改后句子：`The project manager will send copies of the slides to attendees.`。有效删除 1 次，随后重新检查 1 次；结果为 `pending`，`recordable=True`，deadline 为 null。

这证明修正及复查路径能工作，且未把未确定的时间写回；它不证明自然输入下的调用稳定性。受控诊断的主任务没有发布成新的会议输出。

[完整输入、请求、trace、结果](../revision-probe-online-v1/)。首次沙箱尝试因 WinError 10013 无法联网，保存在 ../revision-probe-v1/，不计入在线成功样本。

## 基于原文的内容抽查

以下是 Codex 对原文和阶段产物的检查，不代表新增的人工确认。细节及证据见 [source_spot_checks.json](source_spot_checks.json)。

- **QUALITY_GAP** IS1008a 的 slides Action 已通过抽取、合并与模型选择，却因 priority=1、Action 上限为 5，被排第 7 而截掉。
- **NEEDS_SEMANTIC_REVIEW** IS1008a 的 UI 任务仍写成负责协调，但已交付的原文否认处于协调者位置。owner/status 待确认没有消除正文中的这一限定，需语义复核。
- **SUSPECTED_P2_OVERBLOCKING** TS3006a 中 PM 分配 working design，ID 回应 True；statement 已通过，但 conflict 以“未发现矛盾、证据有限”为由保持 uncertain。Agent 因缺少 deadline 选择澄清，任务未进入主记录，疑似过度拦截。
- **POSITIVE_CHECK** TS3006a 的售价、销售额目标、生产成本已分别保留为三条 Decision。
- **COVERAGE_GAP** TS3006b 实际检索了旧条目并读取对话，但最终没有发布跨会议关系；此前人工确认的 TV＋video → 仅 TV 范围更新，本次没有重现为最终关联。读取范围与本轮历史文件哈希仍通过检查。

## 产物与边界

- run/s2/*.json：主记录；同名 *_report.md：可读报告。
- run/s2/*.p2.json 与 *.clarifications.json：检查、待确认状态及问题对应关系。
- run/s2/*.stages.jsonl / *.trace.jsonl：阶段、真实 API 尝试和 Agent 决策；_attempts/ 保存原始 checkpoint。
- study.json：本次空缓存条件、固定代码/模型/配置；summary.json：结构验证和预算结果。
- 本次只新增试跑产物与核查脚本；UI、标注文档和冻结源码未改动，未 commit。
