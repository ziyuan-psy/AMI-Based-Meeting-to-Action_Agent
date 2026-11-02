# 第八步：开发回归检查

本步骤检查第七步后的生产版本，相对 `agent-speed-precision-eval` 实际代码是否出现质量退步。
它是交付前的小规模开发检查；正式 evaluation 与独立人工复核仍由评估组员完成。
本轮不修改生产提示词、阈值、预算、UI 或标注文件来提高分数。

## 对照口径

- 历史策略固定为 `414a31fe1581b147851ec2bd61b340bf0be26843`，通过 Git 对象导出源码，独立包名加载；不切换工作区分支。
- 当前策略使用第七步后的生产代码。本步骤新增的回归脚本不改变生产调用入口。
- 默认会议为 IS1008a、TS3006a、TS3006b，模式为 s2；两个版本均按各自顺序生成历史记录。
  不跨版本注入历史输出。选择 s2 时必须包含同系列全部更早会议。
- 冻结同一份转写、参与者、scenario 文本和开发 gold。要求两版解析结果逐项一致。
  gold 位于独立评分目录，不传给 runtime、Store 或 Agent 工具。
- 两边缓存从空目录开始。仓库原有 MVP 输出不作为本轮运行结果。
- 历史抽取、合并、提示词、工具及 Agent 决策来自固定源码；统一使用当前 LLM transport，
  使超时、重试和并发计入相同预算。历史 normalize 外加观察器保存结果，不改写内容。
  因此这是历史策略与统一受控 transport 的对照，不是旧版 SDK 重试行为的计时复刻。
- 同一模型；每场保留 80 次模型尝试、100 万 token、300 秒请求超时、1,200 秒整场超时和 4 路并发。
  Agent 自主选择检索、再次搜索和停止。评分有独立预算，与生成用量分开报告。

## 评分与解释

沿用 evaluator 的同类型、一对一语义匹配与三次模型投票。评分器不接收版本名；相同文本列表
在本轮复用相同投票，保存每次原始匹配及最终映射。模型评分尚未独立人工复核。

同时比较旧版最终记录、当前最终记录和当前 normalize 后的 P2 前条目：

- 最终记录比较回答实际输出是否更接近同一份 gold。
- P2 前后比较帮助定位候选/合并漏项与检查隔离；P2 前条目不可作为正式输出。
- 保存匹配项及未匹配项，避免只有一个汇总 F1。
- NA/N/A 占位的整个会议×类型同时不计分。提示词示例及对应系统项共同排除，沿用 `full_eval.py`。
- 未完成的 pipeline 不作为空输出计分；两边都完成才进入配对汇总。正常完成后被 P2 隔离的条目属于实际输出减少。
- 字段准确率只在匹配项及可用 gold 标签上计算，跳过 `unreviewed` 与状态占位。
- 对话支持率由现有 support judge 单独判断，每项最多前 10 条对话引用；不评价 PDF 引文的语义正确性。

gold 包含待人工复核的修订和未复核草稿，分数不能作为正式准确率。本步骤不使用 ES2004/TS3007 留出集。
每边只运行一次，不能估计稳定性或统计显著性。摘要随流程生成保存，本轮不额外做摘要语义评分。
两版抽取为独立新采样，前后差异同时包含实现差异和模型随机性，不能把每个分数变化都归因于代码。
当前版 P2 前后使用同一次 normalize 结果，可以直接追踪哪些输入被检查保留或隔离。

## 产物和运行

结果见 [regression-v2/REGRESSION.md](../results/development_regression/regression-v2/REGRESSION.md)。
`study.json` 保存版本、配置及哈希；`source_baseline/`、`source_current/`、`inputs/`、`gold/` 保存快照。
`<arm>/<meeting>.result.json` 保存实际状态与用量。`comparison.json`、`audit.json` 保存统一汇总与逐条审计。

当前版原生 `.run.json`、`.stages.jsonl`、`.p2.json`、`.clarifications.json` 和 checkpoint 保留候选、
合并、投票、截断、检查、工具返回、引用、关系见证和停止原因。两版 `.requests.jsonl` 保存模型实际请求；
当前响应见 stages，历史响应及控制事件在同一 requests 文件中。`scoring/` 单列对齐投票、支持判断和评分预算。

已有跨会议人工确认案例与第七步文档案例保持冻结，其真实响应回放包含在完整测试中。
整场自然运行是否调用相应工具，以本轮 trace 为准。

首轮 `regression-v1` 的旧版三场已完成；当前版在零模型调用时遇到 Windows 路径限制：
268 字符的原子写入临时文件路径导致首个 checkpoint 保存失败。修复把临时名改成唯一 UUID，
保留同目录原子替换及清理机制，新增长路径/长文件名测试。第二轮 `regression-v2` 记录该修复，
原样复用已完成的旧版真实产物（见 `baseline_reuse.json`），只重跑当前版与评分。
复用要求输入、gold、历史策略、模型、配置及共享 transport 一致；原调用、时间及失败记录均保留。

在仓库根目录使用现有依赖和 API 环境，设置 `PYTHONPATH=agent/src`，目的目录必须是新的：

```powershell
python agent/scripts/development_regression.py prepare agent/results/development_regression/new-run
python agent/scripts/development_regression.py baseline agent/results/development_regression/new-run
python agent/scripts/development_regression.py current agent/results/development_regression/new-run
python agent/scripts/development_regression.py score agent/results/development_regression/new-run
```

各阶段拒绝覆盖尝试，失败保留原始产物。准备后若源码、配置、输入或快照改变，将拒绝运行以防混用版本。
相同模型配置仍可能产生不同输出。本轮不调参追分，发现的退步应作为后续修复与交付冻结的依据。
