# 选择阶段：明确重复项缺少 reason 的修复

本轮已结束：四场都实际尝试，**a/b/c 完成，d 在独立的合并成员类型错误处失败**。
b 的真实响应自然缺失 5 个重复项 reason，适配后一次选择提交通过并完成全流程。
400 项测试通过，原始失败响应零 API 回放通过。d 的 members 返回对象而非候选编号，
已零 API 复现并保留 partial，没有顺带修改合并逻辑或覆盖重跑。
完整结果见 [REPORT.md](ts3006-v1/REPORT.md)，d 诊断见 [consolidation_failure.json](ts3006-v1/consolidation_failure.json)。

基于 agent-development@787d88e，仅修改 item_selection.py 的元数据适配与缓存命名空间，
不改变模型、提示词、schema 必需字段、数量上限、P2 来源验证或整场预算。

仅当模型明确选择 keep=false、claims 为空或省略、duplicate_of 指向其他有效保留目标时，
可从该指针生成 code_metadata 说明。目标的类型、owner 与来源兼容性仍由原逻辑检查；
普通排除、保留项、无效目标、矛盾选择和显式无效 reason 不自动补齐。原始参数保持原样，
每次适配保存在 select/attempt.normalizations。

## 离线验证

- replay.json：同一真实输入和两份失败响应，旧代码在两次提交后失败，修复后第一份即可完成选择。
  两份原响应的 canonical 校验均通过；首次模型请求完全一致，新增 API 调用为 0。
- tests.log：400 项测试通过，包括原响应、来源覆盖、类型/owner 护栏、非法目标、缺失理由边界、旧缓存隔离。
- agent/tests/fixtures/selection_missing_reason.json 保存真实候选、模型响应及原 Store 角色映射，
  与原选择输入核对一致；没有用 gold 标注作回放输入。

## 新空缓存完整重跑

ts3006-v1/ 从相同 TXT 重新运行 TS3006 a→d，a 记录也重新生成。没有复制旧候选、
响应缓存或历史记录。每场仍为 80 次调用、100 万 token、请求超时 300 秒、整场超时
1200 秒、并发 4、重试 2。state.json / campaign.jsonl 是实时进度；终止后的 REPORT.md
与 summary.json 才是整轮审计结果。

这是一轮独立的四场重跑，不与旧十六场回归拼接成同版 16/16。旧回归、UI 和标注文档保留，
不运行留出集，不计算 F1，不 commit。

```powershell
python agent/results/select_reason/replay.py
python agent/results/select_reason/campaign.py prepare agent/results/select_reason/new-run
python agent/results/select_reason/campaign.py run agent/results/select_reason/new-run
python agent/results/select_reason/campaign.py progress agent/results/select_reason/new-run
python agent/results/select_reason/audit.py agent/results/select_reason/new-run
```

研究驱动固定在 787d88e 加当前源码补丁，每次要求全新目录。source/、source.patch、study.json
保存本轮代码与配置；offline/ 保存离线验证，run/ 保存在线结果。凭据仅通过环境提供。
