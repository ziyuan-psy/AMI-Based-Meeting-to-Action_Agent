# 四系列十六场：完整 S2 开发回归

`787d88e-v1/` 是基于 `agent-development@787d88e` 的一次空缓存原生 pipeline 运行。
系列为 ES2011、IS1008、TS3004、TS3006，每个系列 a→d，系列间顺序执行。

本轮结果：实际尝试 14 场，**13/16 完成（81.25%）**。TS3006b 的两次选择提交均遗漏
重复排除条目的必需 reason，校验失败；TS3006c/d 按原串行规则阻塞，没有实际运行。
十三场成功输出及失败 partial 的产物审计通过。详细结果见 [REPORT.md](787d88e-v1/REPORT.md)，
关联来源及语义复核清单见 [CROSS_MEETING.md](787d88e-v1/CROSS_MEETING.md)。没有修复后重跑或拼接结果。

这次只运行 S2，从原始 TXT 开始，真实执行抽取、合并、选择、P2、摘要、验证及报告。
未复制既有响应缓存或旧历史记录，未读取 gold 评分，未运行留出集。保持原有模型、配置、
预算及原生重试；没有额外单条重复、注入错误、补造关联或运行后调参。

`state.json` 与 `campaign.jsonl` 记录实时进度；只有终止后的 `summary.json` / `REPORT.md`
才是整轮核查结果。失败时保存已有产物，按照原生 CLI 策略停止同系列后续会议并继续下一个系列。
后续会议受阻不能当作已完成；来源校验通过不能当作关系语义已人工确认。

从仓库根目录、原项目依赖环境运行（凭据只从环境提供）：

```powershell
python agent/results/s2_dev16/campaign.py prepare agent/results/s2_dev16/new-run
python agent/results/s2_dev16/campaign.py run agent/results/s2_dev16/new-run
python agent/results/s2_dev16/campaign.py progress agent/results/s2_dev16/new-run
python agent/results/s2_dev16/audit.py agent/results/s2_dev16/new-run
python agent/results/s2_dev16/diagnose_selection.py agent/results/s2_dev16/787d88e-v1
```

prepare 必须使用不存在的新目录，并验证当前分支和源码固定在 787d88e；run 只接受 PREPARED
状态，不允许覆盖或继续消耗同一场会议的新预算。模型各次输出可能不同。
本轮不修改生产代码、UI、标注文档，不 commit。完整 API 尝试与重试以原生 runtime_control
和 stages 为准；requests.jsonl 只记录逻辑模型输入。
