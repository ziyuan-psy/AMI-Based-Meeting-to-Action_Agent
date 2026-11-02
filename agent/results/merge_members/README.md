# TS3006d 合并成员类型修复

分支 `agent-development`，基于 `787d88e`，尚未 commit。保留上一轮选择阶段的重复项元数据修复。本轮生产改动仅在 `agent/src/mta/extract.py`。

模型返回的合并 `members` 应为候选整数编号。原 TS3006d 的实际响应包含 `{"id":"MTD021PM"}` 对象，旧代码先执行 `dict.fromkeys()`，因此出现 `TypeError: unhashable type: 'dict'`，整场退出。

修复先校验成员类型，非法分组整组拒绝并记录原因，同批有效分组继续使用。成员不会被猜测或转换成候选编号。没有可用条目时在原每样本最多三次的范围内增加格式纠正信息；所有样本无效仍明确失败。原有调用/token/超时/并发预算与异常传播保持不变。有原响应的旧缓存会重新校验。

## 离线证据

- `capture.py` / `before.json`：修复前捕获两轮旧回归的 1,094 份产物哈希及父级驱动/文档、28 个受保护文件。
- `agent/tests/fixtures/consolidation_bad_members.json`：原 TS3006d 三个真实响应和实际候选，未读取 gold。
- `replay.py` / `replay.json`：旧代码复现 attempt 17 的异常；新代码拒绝其两组非法成员。attempt 18 的 11 条有效结果完全不变；attempt 19 保持无可用结果。原始响应不改写，零 API 调用。
- `tests.log`：`agent/tests` 全部 428 项测试通过，67.49 秒，零真实模型调用。
- `test_merge_members.py` 覆盖非法成员与响应结构、同批有效结果保留、原重试上限、共享预算退出、缓存原响应重新校验。

逐响应回放没有虚构模型后续的纠正结果；完整运行是否完成以新回归的实际记录为准。

## 新的真实回归

目录：`ts3006-v1/`。已完成 **4/4**，产物审计 **PASSED**。共 236 次真实 API 尝试、2,232,229 token；每场均未超出原预算。结果见 [REPORT.md](ts3006-v1/REPORT.md)、`summary.json`、`verification.json` 和 `manifest.json`。

本轮生成 2 条跨会议关联（语义待人工核对）、57 条澄清请求。P2 输入 50 条，17 条 accepted、33 条 pending，41 条可记录；没有自然触发有限修正。在线没有再次返回对象类型成员，因此该类型错误路径的证据来自原始失败响应回放和单元测试。

- 空缓存 TS3006 a→d，每场从 TXT 经过解析、抽取、合并、选择、P2、Agent 和摘要。
- 保持 `@30875-fall26-openai/gpt-5-nano`、Portkey endpoint、现有配置。
- 每场最多 80 次调用、1,000,000 token；请求 300 秒、整场 1200 秒、并发 4、传输重试 2。
- 只读取本轮已完成的同系列更早记录。没有复制旧候选、响应、缓存或历史产物。
- 一次运行；失败则阻塞同系列后续场次，保留失败原文、checkpoint 和 partial，不替换或重跑择优。
- 新目录保存源代码快照、两个未提交源文件的补丁、输入/模型/配置/测试哈希和所有运行产物。

本轮没有重跑其他十二场，不与旧 13/16 或上一轮 3/4 拼接为同版 16/16。结构/来源验证不等于语义正确性或 F1 提升；关联尚无本轮新增人工确认。

## 复查命令

在仓库根目录设置已有依赖路径，不打印凭据：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location).Path 'agent\logs\trace_step_deps')
& '.\.venv-ui\Scripts\python.exe' -X utf8 agent/results/merge_members/replay.py
& '.\.venv-ui\Scripts\python.exe' -X utf8 agent/results/merge_members/campaign.py progress agent/results/merge_members/ts3006-v1
& '.\.venv-ui\Scripts\python.exe' -X utf8 agent/results/merge_members/audit.py agent/results/merge_members/ts3006-v1
```

`audit.py` 只读本地开发输入和产物，不调用模型，要求完整运行已结束。`campaign.py prepare/run` 会拒绝覆盖已有目录、重跑已开始的 cohort，或改变冻结代码、模型与配置；在线运行需要已有 API 凭据。

UI、标注文档、留出集和两轮原回归记录均不作修改。没有 commit、stage 或 push。
