# 本轮开发回归

- `repair-v1/`：首轮真实尝试。IS1008a 完成但仍遗漏 slides，进一步定位到空负责人和核验前排序的问题；TS3006a 因 API 500 在原重试上限后 FAILED，后续会议未运行。失败和全部 checkpoint 保留，未作为成功回归。
- `repair-v2/`：三场完整运行，slides、working design 和有限修正诊断均通过；实际检索/读取后仍因遗漏逐条 links 证据而没有最终关联。完整失败判定和模型原始提交保留，未作为最终成功回归。
- `repair-v3/`：补齐 links 后的试跑。IS1008a 完成；TS3006a 在选择阶段因明确排除的候选省略空 claims 数组而退出。两份失败响应完整保存，未作为完整成功结果。
- `repair-v4/`：兼容空排除 claims 后的试跑。两份 v3 原响应重放均通过；IS1008a 又暴露了不适用关系检查和缺少动作提示造成的误隔离，TS3006a 因上游 API 500 按原上限退出。
- `repair-v5/`：补齐代码已知的检查适用性、保守缺省动作及非正向元数据处理后，同输入空缓存完整试跑加独立有限修正诊断。以 [REPORT.md](repair-v5/REPORT.md) 和 [summary.json](repair-v5/summary.json) 为结果依据。captured_p2_metadata_replay.json 验证 v4 的两份真实 slides 提交在不增加正向证据的情况下可保留任务。
- [CHANGES.md](CHANGES.md)：代码、测试、候选核验与最终数量上限的说明。

五轮均对照 `agent/results/frozen_smoke/fresh-v1`，该目录和它的完整父目录清单保持原样。运行目录互相隔离，没有把某轮的成功输出拼接成另一轮的完整结果。UI 和标注文档的原始哈希在 protected_before.json。v5 的驱动仅额外保存脱敏后的上游错误详情，不改变调用、重试或超时。

```powershell
$studyPath = 'agent/results/smoke_repairs/new-run'
python agent/results/smoke_repairs/run_study.py prepare $studyPath
python agent/results/smoke_repairs/run_study.py run_with_probe $studyPath
python agent/results/smoke_repairs/audit_study.py $studyPath
```

`prepare` 保存当前源码、输入、模型、配置、driver 和原试跑哈希，不调用模型；`run_with_probe` 通过已有 API 执行完整真实流程，再单独诊断有限修正；`audit_study` 不调用模型，检查最终 JSON、报告、trace、澄清、预算和关联来源。目录必须是新路径，driver 哈希固定后不得修改。API 凭证仅由环境提供。

每场仍为 80 次调用、100 万 token、1,200 秒，请求上限 300 秒，并发 4，传输重试 2；最终 Action/Decision/Problem 数量限制仍为 5/7/4。新的 Action 候选核验池最多为最终上限的两倍，额外核验仍受同一预算约束。

这不是最终 evaluation：本轮在线回归不读取 gold，不计算 F1，不使用留出集。相同输入的新模型运行存在输出变化；确定性旧池重放和边界测试用于单独验证控制逻辑。
