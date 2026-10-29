# 第八步：开发回归检查

**结论：本轮发现质量或整场完成率退步，需要修复后再考虑冻结交付。**
已完成配对评分 2 场；当前版未完成：TS3006b (call_budget_exhausted)。F1 下降类型：actions, decisions, problems。
P2 证据选择协议未完成是明确问题：模型多次标记 pass 却没有选择当前对话 ID。现有验证保持严格，未通过自动补证据或放宽预算来追分。
代码回归为 240 项测试和 11 个子测试通过；本轮修复了 Windows checkpoint 临时路径长度问题。测试通过不代表语义质量通过。

本轮对照使用队友分支实际代码与第七步后的生产版本，保存全部尝试；不使用仓库旧 MVP 输出代替新分支结果。
这是三场 SB 开发会议的一次对照，不是最终 evaluation、独立人工复核或稳定性检验。

## 固定口径

- 队友代码：`414a31fe1581b147851ec2bd61b340bf0be26843`；当前生产代码基于 `df0e6ed3fdede653e815fa8d03dec12dd0b4f250`，本轮增加回归工具，并修复 checkpoint 原子写入临时路径长度问题。
- 会议：IS1008a, TS3006a, TS3006b；s2 顺序运行，同一版本仅使用自己的早期会议输出。
- 两版解析结果完全相同；转写、participants、scenario 和当前开发 gold 均已复制并计算哈希。
- 两边缓存从空目录开始。历史策略、提示词和工具来自冻结源文件；历史分支统一接入当前 LLM transport，以执行相同调用、token、超时、重试及并发限制。该接入不修改其抽取、合并或 Agent 决策逻辑。
- 同一 Portkey gpt-5-nano 模型。评分沿用现有同类型、一对一、三票多数语义匹配；相同文本列表复用本轮投票。NA 占位类别不计分，提示词示例及对应输出共同排除。
- gold 是现有开发草稿，部分内容待人工复核；只有 evaluator 读取标签。ES2004/TS3007 留出数据未用于本轮。
- 每场仍为 80 次模型尝试、100 万 token、300 秒请求超时、1,200 秒整场超时和 4 路并发。评分有独立的同上预算，不与生成用量混在一起。
- 未完成的会议不按空输出计分；两版均完成后才进入配对指标。P2 正常完成但隔离的条目则算作实际输出减少。

## 条目质量

括号内为 TP / 系统条数 / gold；汇总为 micro P/R/F1。current_before_p2 是当前 normalize 后的候选，不是可交付输出。

| 类型 | 队友 P / R / F1 | 当前 P / R / F1 | 当前 P2 前 P / R / F1 |
| --- | --- | --- | --- |
| actions | 0.700 / 0.875 / 0.778 (7/10/8; 2 场) | 0.333 / 0.125 / 0.182 (1/3/8; 2 场) | 0.500 / 0.625 / 0.556 (5/10/8; 2 场) |
| decisions | 0.400 / 0.333 / 0.364 (2/5/6; 2 场) | 0.250 / 0.167 / 0.200 (1/4/6; 2 场) | 0.250 / 0.333 / 0.286 (2/8/6; 2 场) |
| problems | 0.250 / 1.000 / 0.400 (1/4/1; 2 场) | 0.000 / 0.000 / 0.000 (0/1/1; 2 场) | 0.200 / 1.000 / 0.333 (1/5/1; 2 场) |

已计分会议：IS1008a, TS3006a；未计分会议：TS3006b。

## P2 与待确认

| 会议 | 接受 | 待确认 | 隔离 | 仍可记录的待确认 | 澄清请求 | 文档观察/保留 | 已验证历史关系 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| IS1008a | 1 | 8 | 7 | 1 | 23 | 0/0 | 0 |
| TS3006a | 0 | 14 | 8 | 6 | 32 | 0/0 | 0 |

以下是评分在 P2 前匹配到 gold、随后被隔离的输入，须核对是正确拒绝了有问题的候选，还是过度拦截。自动匹配并非事实正确性的证明。

- `IS1008a#0` → `IS1008a.JacquelinePalmer.s.14`：ID(FIE073) did not understand the project goal and believed the project was a different product, indicating a scope/goal misalignment. 停止：`no_progress`；未解决检查：`{"statement": "current_meeting_support_required", "status": "current_meeting_support_required", "conflict": "current_meeting_support_required"}`。
- `IS1008a#1` → `IS1008a.JacquelinePalmer.s.9`：ID will deliver a written plan covering functional and technical design, staffing, timeframe, budget, and market plan by two weeks. 停止：`no_progress`；未解决检查：`{"statement": "Current dialogue shows discussion about delivering a written plan and timeframe, but no explicit, binding commitment to a two-week deadline by ID; references to two weeks appear as a question and general expectation, not a firm due date.", "owner": "Owner is listed as FIE073 in the record, but the observed dialogue does not contain an explicit assignment or acknowledgment by FIE073 as owner; ownership is evidenced by PM remarks requesting a written plan, not assignment.", "deadline": "current_meeting_support_required", "status": "No explicit statement of current status beyond 'open' in the record; dialogue indicates ongoing planning but no final status change.", "conflict": "current_meeting_support_required"}`。
- `TS3006a#1` → `TS3006a.manual.s.1`：The Project Manager will summarize the discussed items and place the summary in the project folder. 停止：`invalid_action_limit`；未解决检查：`{"statement": "not_checked", "owner": "not_checked", "deadline": "not_checked", "status": "not_checked", "conflict": "not_checked"}`。
- `TS3006a#4` → `TS3006a.elana.s.11`：The Industrial Designer will work on the conceptual design. 停止：`no_progress`；未解决检查：`{"statement": "current_meeting_support_required", "owner": "current_meeting_support_required", "deadline": "deadline_unresolved", "status": "current_meeting_support_required", "conflict": "current_meeting_support_required"}`。
- `TS3006a#6` → `TS3006a.elana.s.10`：The Marketing Expert will conduct internet searches and user interviews to determine user requirements. 停止：`invalid_action_limit`；未解决检查：`{"statement": "not_checked", "owner": "not_checked", "deadline": "not_checked", "status": "not_checked", "conflict": "not_checked"}`。
- `TS3006a#9` → `TS3006a.elana.s.13`：Pricing will be set at 25 Euros, with a target of 50 million Euros in sales and production cost equal to half of the selling price. 停止：`no_progress`；未解决检查：`{"statement": "current_meeting_support_required", "status": "current_meeting_support_required", "conflict": "current_meeting_support_required"}`。

### 协议遵循诊断

有些检查由模型标为 pass，却没有选择 quotes 中的对话 ID。代码因此保留 current_meeting_support_required。
这类失败表明证据选择协议未完成，不能直接解释成原始会议没有支持证据。修复不能通过自动认定支持或跳过验证来完成。
原始 pass 未选引用的观察数还包含允许从 statement 派生的 status/conflict 检查，并不全部是错误；最终 statement 被隔离的第二个数量才是明确的阻塞症状。

- IS1008a：8 条出现过 pass 未选引用；4 条最终因 statement 缺少当前引用而隔离：`IS1008a#0, IS1008a#2, IS1008a#4, IS1008a#7`。
- TS3006a：11 条出现过 pass 未选引用；3 条最终因 statement 缺少当前引用而隔离：`TS3006a#4, TS3006a#9, TS3006a#13`。
- TS3006b：整场未完成，未纳入完整条目的协议统计；部分检查保存在 checkpoint 中。

## 运行与证据支持

| 会议 | 版本 | 状态 | 秒 | 模型尝试 | 已知 token | 未知用量保守计入 | 对话证据 supported/partial/unsupported |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| IS1008a | baseline | COMPLETED | 116.062 | 17 | 110,898 | 0 | 5/0/2 |
| IS1008a | current | COMPLETED | 209.844 | 46 | 368,145 | 0 | 0/2/0 |
| TS3006a | baseline | COMPLETED | 155.718 | 12 | 132,901 | 0 | 12/0/0 |
| TS3006a | current | COMPLETED | 286.375 | 80 | 587,141 | 0 | 6/0/0 |
| TS3006b | baseline | COMPLETED | 226.563 | 28 | 268,607 | 0 | 未计分 |
| TS3006b | current | BUDGET_EXCEEDED | 329.844 | 80 | 681,528 | 74,042 | 未计分 |

以上为生成用量，评分用量在各 `scoring/<meeting>.json` 中单列；unknown usage charge 不是供应商报告的真实 token。
Owner、deadline、status 的逐会议匹配项统计保存在 [audit.json](audit.json)，未复核占位字段不计入准确率。
摘要随完整流程生成并保存，本轮未额外对摘要做语义评分。PDF 引用验证证明来源与选用关系，不能替代语义正确性判断。

## Trace 与复查入口

- [完整对照、匹配、未匹配条目与隔离原因](comparison.json)。
- [逐会议字段、证据支持及 trace 覆盖审计](audit.json)。
- `current/s2/<meeting>.run.json`：代码、模型、配置、实际预算与所有 checkpoint 路径。
- `current/s2/<meeting>.stages.jsonl`：抽取候选、合并采样、投票、截断、normalize、调用与终止事件。
- `current/s2/<meeting>.p2.json` / `.clarifications.json`：检查结论、工具返回、选用证据、停止原因、记录对应关系及待确认问题。
- `<arm>/<meeting>.requests.jsonl`：模型实际请求；当前响应另见 stages，历史版本的响应与控制事件在同一 requests 文件中。
- `baseline/s2/`、`current/s2/`：最终 JSON 与 Markdown；`source_baseline/`、`source_current/`：本轮源文件快照。
- [study.json](study.json)：选择口径、输入与源码哈希；[parser_parity.json](parser_parity.json)：解析一致性。

此前人工核对的跨会议案例和第七步文档案例仍单独冻结，本轮全流程是否自主用到这些工具应以实际 trace 为准；没有调用不能写成已通过场景验证。

## 后续优先级

1. 修复普通 P2 检查的证据选择/提交协议，确保模型可以提交有效当前对话 ID；保留真实来源校验，不能自动把检索命中视为支持。
2. 减少重复无进展和无效格式回合，使整场在原有 80 次/100 万 token 内完成，并为后续阶段保留调用余量。
3. 先用保存的 normalize 输入定位 P2 前后变化，再按同一口径复跑整场；同时检查初始抽取和合并的差异，不能把独立采样的全部变化归因于 P2。
修复和复验之前不建议冻结交付。上述 gold 匹配与证据判断仍需评估组员独立检查。

## 复现

从仓库根目录设置 `PYTHONPATH=agent/src`，配置项目现有依赖和 API 环境。目的目录必须是新的：

```powershell
python agent/scripts/development_regression.py prepare agent/results/development_regression/new-run
python agent/scripts/development_regression.py baseline agent/results/development_regression/new-run
python agent/scripts/development_regression.py current agent/results/development_regression/new-run
python agent/scripts/development_regression.py score agent/results/development_regression/new-run
```

各阶段拒绝覆盖已有尝试。再次在线运行可能产生不同结果；固定版本不意味着模型输出逐字相同。
本轮不调提示词、阈值或预算来追分。发现的质量退步应作为后续修复与交付冻结的依据。
