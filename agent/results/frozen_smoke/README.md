# 3b886f6 冻结版空缓存完整试跑

固定分支 `agent-development`，提交 `3b886f6c0c1520a1c5bc8628da7198a871c25495`。本目录只保存新运行及核查产物，没有修改生产源码、UI 或标注文档，也没有 commit。

- [主试跑报告](fresh-v1/REPORT.md)：三场从 TXT 到最终 JSON/报告的完整真实运行。
- [机器可读汇总](fresh-v1/summary.json)：预算、真实阶段调用、零历史响应缓存命中、结构/来源校验及验收缺口。
- [自然有限修正调用](fresh-v1/limited_revision_cases.json)：一次空参数调用被协议拒绝，没有自然成功修正案例。
- [独立受控诊断](revision-probe-online-v1/run.json)：在真实 slides 证据上注入无依据的 Friday，3 次真实调用成功删除限定并重新检查；保留时间待确认。该案例不计入自然运行或评估。
- [原文抽查](fresh-v1/source_spot_checks.json)：slides 截断、疑似 P2 过拦截、正文限定风险，以及本次未重现的最终跨会议关联。不是新增的人工确认。
- `revision-probe-v1/`：首次沙箱网络失败的原始尝试，WinError 10013；与在线诊断分别保留。

完整流程、产物结构检查通过，但尚不能据此认定内容验收通过。本次未读取 gold 打分，不提供 F1 对比或最终 evaluation 结论。

## 复现新的在线完整试跑

在仓库根目录按冻结包说明安装依赖，并通过环境提供 API 凭证。使用原冻结入口；输出目录必须是全新路径：

```powershell
python agent/results/quality_repairs/release-v9/run_frozen.py --verify-only
python agent/results/quality_repairs/release-v9/run_frozen.py --output agent/results/frozen_smoke/fresh-v2/run
```

本次主试跑实际参数为 `--output agent/results/frozen_smoke/fresh-v1/run`。默认顺序为 IS1008a、TS3006a、TS3006b；使用 Portkey 的 `@30875-fall26-openai/gpt-5-nano`。每场为独立的 80 次调用、100 万 token、1,200 秒预算；请求上限 300 秒、并发 4、传输重试 2，均保持冻结配置。

## 离线重新核查本次产物

```powershell
python agent/results/frozen_smoke/audit.py agent/results/frozen_smoke/fresh-v1
```

该命令不调用模型；会重建报告及汇总并重新验证冻结源码/输入、主产物 schema、P2 来源、重新检查步骤、历史文件哈希、trace 连续性和受保护文件。当前本地使用 `.venv-ui/Scripts/python.exe`，依赖位置通过 `PYTHONPATH=agent/logs/trace_step_deps` 指定；复现时可按冻结包文档安装同版本依赖。

`probe_limited_revision.py` 只用于独立受控诊断，不是主入口，也不替代从 TXT 开始的真实试跑。再次运行时必须指定新目录；不会覆盖本次结果。
