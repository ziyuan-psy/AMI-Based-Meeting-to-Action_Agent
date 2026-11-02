# 选择重复条目元数据修复：TS3006 四场空缓存 S2 回归

基于 `787d88e34d803a3bcafa4817720634f2dd74b50e`，仅修复 item_selection.py 的重复项元数据处理，补丁见 source.patch；分支 agent-development。未 commit，UI 和标注文档未改。

范围：TS3006 a→d。原生 pipeline 从 TXT 真实运行；新目录、新缓存、未复制旧记录、未扣历史预算。模型、提示词、配置、整场预算和原生重试保持不变。未读取 gold 或留出集转写。

完成 **3/4（75.00%）**，实际尝试 4 场；产物审计 **PASSED**。失败同系列的后续会议按原生串行策略阻塞，其他系列继续；不替换历史、不重跑择优。

| 会议 | 状态 | 秒 | 模型尝试 | 计入 token | 可记录/P2 输入 | 澄清 | 发布关联 |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| TS3006a | COMPLETED | 317.7 | 38 | 328324 | 13/14 | 7 | 0 |
| TS3006b | COMPLETED | 469.3 | 64 | 645127 | 9/11 | 10 | 2 |
| TS3006c | COMPLETED | 432.8 | 78 | 725521 | 10/13 | 16 | 1 |
| TS3006d | FAILED | 228.25 | 21 | 366880 | —/— | — | — |

总模型尝试 201；已知 token 2,019,930，计入 token 2,065,852（未知用量保守计入 45,922）。计入 token 不等于供应商账单。

## 真实调用阶段

| 会议 | 抽取 | 合并 | Decision 重合并 | 选择 | P2 | 自主工具决策 | 摘要 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TS3006a | 8 | 4 | 3 | 1 | 21 | 0 | 1 |
| TS3006b | 12 | 4 | 4 | 1 | 20 | 22 | 1 |
| TS3006c | 13 | 3 | 3 | 1 | 33 | 24 | 1 |
| TS3006d | 17 | 4 | 0 | 0 | 0 | 0 | 0 |

## 检查与限制

- 完成场次逐个校验原生 JSON/schema、P2 与澄清映射、报告问题覆盖、文件哈希、阶段/Agent trace、TXT→解析 checkpoint 和调用/token 账本。
- 每场 80 次调用、100 万 token、请求超时 300 秒、整场超时 1200 秒、并发 4、重试 2；每个系列后续会议只读取本轮已发布的同系列更早记录及原文。
- stages 和 runtime_control 是实际尝试与重试的依据；requests.jsonl 记录逻辑请求，不应当成供应商调用次数。
- 关联 witness 校验旧记录、双方原文及最终对应关系；来源验证通过不等于关系语义已人工确认。本轮不计算 gold P/R/F1，不作为最终 evaluation 或稳定性保证。
- 发布关联 3 条；逐条旧记录→工具返回→Agent 判断→新记录见 [可读关联清单](CROSS_MEETING.md) / cross_meeting_links.json，未决关系见 pending_relations.json。
- P2 结果：38 条检查条目，accepted 17、pending 21；可记录 32 条，其他未发布条目不能当作已完成内容验证。澄清请求 33 条。
- 自然运行有限修正 0 次；事件及重新检查见 limited_revisions.json。没有额外错误注入或单条重复。

## 失败与审计问题

- TS3006d：FAILED；阶段 consolidate；原因 stage_failed；已有 checkpoint 和 partial 见该场 result.json。

### d 场新增的独立合并故障

TS3006b 的缺失 reason 已在本轮五条真实重复项中补齐，一次选择提交即通过，并完整运行 P2/摘要。a、b、c 完成；d 在选择之前的合并阶段失败。
原始合并响应的 members 应是候选整数编号，却包含 {"id":"MTD021PM"} 字典。现有 _merge_groups 在过滤整数之前执行 dict.fromkeys，产生 unhashable type: dict。原响应零 API 回放已复现；extract.py 与 787d88e 完全相同，是新覆盖发现的独立缺陷。
d 已实际运行 21 次调用，计入 366,880 token，尚未耗尽预算；其中 45,922 是取消时在途请求的未知用量保守预留。parsed/extracted、所有已返回响应及 partial 均保留。
本轮没有顺带修改合并逻辑或猜测字典对应哪个候选，没有修后覆盖重跑。具体失败位置及同批有效响应见 consolidation_failure.json。

## 本轮选择阶段

| 会议 | 选择提交次数 | 缺失重复项说明的元数据补充 | 未通过的选择提交 |
| --- | ---: | ---: | ---: |
| TS3006a | 1 | 0 | 0 |
| TS3006b | 1 | 5 | 0 |
| TS3006c | 1 | 0 | 0 |
| TS3006d | 0 | 0 | 0 |

这里只记录本轮自然触发的补充；原响应回放不计入在线数量。原始参数保持缺失状态，每次代码补充均保存 reason_origin=code_metadata；没有生成新的选择、目标、事实或证据。

## 原始失败回放与验证范围

原 TS3006b 两份选择响应在旧代码下两次失败；修复后第一份响应一次完成选择，第二份原响应也通过 canonical 校验。首次模型请求完全相同，未新增 API 调用，原始参数保持不变。
400 项测试通过。修复只补充明确指向有效保留目标的重复排除元数据 reason；继续执行原有类型、owner、候选来源与 P2 证据验证。模型、配置和预算完全一致，缓存使用单独的元数据版本。
旧十六场回归及 825 份清单产物保持原样。本轮四场独立执行，不拼接为一次同版十六场全部通过；新版本的其他十二场未在本轮重跑。
离线回放和测试见 offline/，新主流程见 run/；两者分别记录。

## 原始输入警告

离线复核原生解析 checkpoint 时没有输入警告。

输入警告按原 parser 保留，没有改写原始时间或转写。

## 来源内容抽查

- TS3006c_creative_page_vs_UI_menu：NEEDS_SEMANTIC_REVIEW。Record wording suggests different actors and potentially different tasks: a UI LCD-menu deliverable versus an ID/ME creative-page activity. Same-topic design work does not by itself establish that the same commitment continues. Review task identity and whether this is an in-meeting procedural activity. The fixed metadata adapter did not choose this relationship.
- TS3006b_replaces_minimal_functions：NEEDS_SEMANTIC_REVIEW。The model labels a simple/advanced grouping as replacing the prior minimal-function principle. Grouping and minimizing the number of functions may coexist, so the relation may be stronger than the source establishes. Preserve the actual output and independently review whether replacement is warranted. This is separate from the repaired selection metadata contract.

这些是 Codex 的开发来源抽查，不是新增人工确认；没有修改模型输出或评分，完整引用见 source_reviews.json。

## 复查入口

- study.json / source/ / inputs/：固定代码、模型、配置、输入及哈希。
- run/s2/<meeting>.json、*_report.md、*.p2.json、*.clarifications.json：主记录与待确认项。
- run/s2/*.stages.jsonl、*.trace.jsonl、_attempts/：所有阶段、实际模型响应、工具返回及原始 checkpoint。
- summary.json：每场状态、预算、调用分布、P2 状态、工具与异常；manifest.json / verification.json：最终完整性。
