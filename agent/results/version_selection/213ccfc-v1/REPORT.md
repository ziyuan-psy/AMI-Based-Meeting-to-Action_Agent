# agent-development 版本取舍

本轮完成固定输入的组件取舍，**没有覆盖现有开发源码，也没有切换默认运行策略**。未 commit、未 push。

**建议质量候选：以 414a31f 的内容策略为主体，单独保留 Action 类型恢复，使用原有排序；继续使用现代 parser、预算和 trace。新增选择层与完整 P2 硬门禁暂不进入质量默认。**

这是组件恢复清单，不是已经完成整合和最终 evaluation 的交付版本。现有 P2/检索/文档验证/澄清实现和案例全部保留。

## 最明确的保留证据

| 同一份414真实模型响应，16场 | Action F1 | Decision F1 | Issue F1 | 合计微平均F1 |
|---|---:|---:|---:|---:|
| 414a31f | 0.474 | 0.441 | 0.429 | 0.449 |
| 只保留 Action 类型恢复＋414原排序 | 0.505 | 0.441 | 0.429 | 0.459 |

第二行是固定响应的确定性回放，生成调用为0，仅新增3次评分调用。只新增TS3006a的UI技术功能任务和ME用户需求任务，未删旧条目；两条均核对了原文。ME原候选引用没包含任务句151，应用组合时必须补齐支持引用。本表不代表新组合的空缓存全流程分数。

## 其他独立边界对照

| 对照臂 | 有评分会议 | Action / Decision / Issue / 合计F1 | 同批会议基线合计F1 |
|---|---:|---|---:|
| 类型恢复＋新排序 | 16/16 | 0.520 / 0.441 / 0.429 / 0.463 | 0.449 |
| 再加入 Issue 自动补回 | 16/16 | 0.520 / 0.441 / 0.457 / 0.469 | 0.449 |
| 213ccfc 选择层 | 14/16 | 0.506 / 0.374 / 0.484 / 0.437 | 0.443 |
| 当前 Issue 版选择层 | 15/16 | 0.500 / 0.397 / 0.471 / 0.444 | 0.438 |
| 当前 P2 硬门禁 | 14/16 | 0.358 / 0.359 / 0.407 / 0.370 | 0.425 |

每一行只与同一行的成对基线比较；未评分/生成失败会议不记零分。类型内 NA 类别不计分，排除原来的10条few-shot gold及对应匹配，评分模型、prompt、schema和三次多数票保持不变。

**没有把最高一次F1当作唯一取舍标准。** 新排序＋Issue补回的合计F1 0.469最高，高于建议最小恢复版0.459；Issue的P/R/F1分别从0.455/0.405/0.429变为0.382/0.568/0.457。它净增加22条预测、6个匹配和16条未匹配预测。建议保留扩展候选作复核，不作为默认直接发布；这不是“改动没有作用”。

新排序的Action F1为0.520，稳定排序为0.505；相对基线新增的两个新文本匹配正是同样的TS3006a任务，另外一个匹配来自未改文本的alignment变化。新排序还引入会中传递原型操作。建议选择内容变化最小、没有替换旧条目的恢复方式；现有排序实现继续保留供实验。

边界输入：抽取后处理固定414的候选和三次合并响应；选择层固定同一份414 normalized候选和实际对话；P2固定同一份候选、414 flags和历史记录。历史只能读取同系列更早会议。每个运行臂从原预算中扣除414原抽取/合并的实际调用、token和耗时。

## 抽取证据的适用范围

extraction_audit.json 还复用了414、213及当前Issue版的16场真实候选，逐场确认TXT、模型和配置相同。候选总数（Action/Decision/Issue）：

- baseline414: 73 / 241 / 211
- committed213: 75 / 213 / 213
- issue_current: 70 / 260 / 262

共享gold对话行的统计仅用于定位上游是否触及相关段落，不是语义召回率或F1。不同提示词的历史响应仍含模型采样差异；本轮能明确归因的是同响应的恢复/排序变化，不能声称已证明每一条新提示词比旧提示词差。证据不足的内容策略按事先规则优先恢复414。

## 取舍清单

| 部分 | 取舍 |
|---|---|
| parser、预算、超时、重试/并发、失败保存、trace | 保留 |
| Action 类型转换后的恢复 | 保留；与新排序拆开 |
| 新排序、Issue 自动补回 | 恢复414稳定策略 |
| 新抽取提示、Decision专项重合并及细粒度改写 | 未证明独立稳定收益，质量默认优先414 |
| 新增模型选择层 | 质量默认恢复无额外选择层 |
| 完整P2语义硬门禁 | 保留开发实现；质量候选恢复flag-only跟进 |
| 来源/结构校验、格式修复、检索、文档引用、澄清实现与案例 | 保留，不把未知检查改成pass |

完整P2硬门禁仍未达到质量默认的证据标准。恢复flag-only会降低默认检查覆盖面，交付说明须明确这一点；当前源码未切换，完整P2功能仍在。按函数级恢复内容策略可以保留现代工程能力，整文件回退旧pipeline会带走预算和trace，不能这样实施。

## 原文核对与评分边界

- ES2011b#4：明确要求所有人完成问卷并带新想法，P2却以owner/deadline/验收标准未齐全隔离整条任务。
- ES2011b#11：statement/status已读到“we definitely want a power button and numbers”并判pass，conflict仍拿先前备选机制讨论阻止发布。
- ES2011a：旧matcher把PM任务匹配给ID的gold；原句先点名Industrial Designer。同一PM文本仍在P2输出时，新组matcher又不给匹配。这不是P2删除了这条任务。
- 当前matcher只接收每类条目的文本列表，缓存按整组文本生成key；其他条目或排序变化可能改变未改动文本的匹配。content_and_alignment.json 将这些变化与直接删除/改写分开。

developer_case_review.json 是开发者原文核对，不是组员最终人工evaluation；gold和评分程序未修改。不能把旧TP全部视为原文正确，也不能把所有F1损失归因于P2。

同候选的15场成功P2运行共用444次API尝试；原flag-only Agent为170次。P2检查所有条目，旧Agent只检查flagged条目；这是检查覆盖和消耗的取舍，不能说两种能力相同。

## 完成情况与保存

- 固定16场，独立边界试验48次，成功 46 次；失败保留：[{"meeting_id": "IS1008d", "arm": "p2_current", "status": "FAILED", "failure": {"code": "model_request_failed", "status": "FAILED", "stage": "p2_check", "error_type": "APIConnectionError", "retries_exhausted": true, "http_status": null}}, {"meeting_id": "TS3004a", "arm": "committed213", "status": "FAILED", "failure": {"code": "model_request_failed", "status": "FAILED", "stage": "select", "error_type": "APIConnectionError", "retries_exhausted": true, "http_status": null}}]。
- 评分覆盖：{"baseline": 16, "retention_actions": 16, "retention_actions_issues": 16, "selection213": 14, "selection_issue": 15, "p2_current": 14}。评分中断/预算退出：[{"meeting_id": "IS1008a", "failure": {"code": "meeting_timeout", "status": "TIMED_OUT", "stage": "parse"}, "unscored_arms": ["selection213", "selection_issue", "p2_current"]}]。
- 初始串行评分切到原4并发时，IS1008a一个已准入请求未取得用量，按完整预留计入。原评分文件、成功投票、失败尝试及各次恢复目录均保留。IS1008a恢复计时包含暂停等待，保守触发评分时间上限；未据此给模型输出记零分，也未放宽预算补抽。失败记录里的stage=parse是该评分控制器的默认标签，实际发生于评分恢复等待，与TXT parser执行无关。
- 新生成API尝试 493 次；预算记账token 4,709,158。新增评分尝试 338 次，记账token 1,196,617。未知用量按完整预留记账，不能等同实际账单。
- 107 个评分视图已从实际保存投票零API回放。JSON 503、JSONL 101 全部解析通过。
- 3702 份旧实验产物、28 份UI/标注保护文件、16份开发gold、8 份已有未提交文件哈希不变。留出集未读取，密钥未写入产物。

模型保持 @30875-fall26-openai/gpt-5-nano；80调用、1,000,000 token、请求300秒、整场1200秒、4并发和2次传输重试全部不变。

decisions.json 保存可审阅的函数级取舍与应用条件；study.json、各fixed_inputs、请求响应、评分缓存、verification.json和manifest.json 保存复核依据。当前默认流程仍是原开发实现，没有暗中更改feature配置或提交代码。
