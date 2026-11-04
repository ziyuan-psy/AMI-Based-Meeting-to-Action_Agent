# P2 格式提交与跨会议关联的原因区分

开发基于 `agent-development@3b886f6` 的现有未提交改动；未 commit。UI 与标注文档不动。

## 方法

1. `observed.json` 保存上轮 v14 的 14 次无效提交，以及 v9/v14 的同一跨会议案例初始上下文和行为。
2. `replay.json` 用完全相同的原始提交检查 v9/v14 schema 和当前适配层；用同一份 v14 初检结果分别重放旧/新控制器。没有调用模型，也没有增加正向结论或来源。
3. 固定相同的抽取、合并、选择候选、TXT、参与者、scenario 和 TS3006a 历史记录，在线运行 P2、摘要、原生验证与报告。原上游调用、token、耗时已预留；这不是全阶段空缓存试跑。
4. 同版本独立重复 TS3006b#10 两次，检查首次实际模型请求的 SHA-256 与主流程相同。单条重复是诊断，独立计费/记录，不补填主流程输出，也不能据此估计稳定成功率。

## 已确认的问题与修复

- 同一批坏提交在 v9/v14 的拒绝结果相同；14 次中有 3 次查询规则拒绝、11 次 schema 拒绝。增加不能归因为新增了 6 条严格校验，先前两次完整运行还改变了提示和路径，不能全部归因为随机抽样。
- 只在 `accept` 时给必须检查的历史提供机会，`request_clarification` 却可因 deadline 缺失直接退出，这是已有控制缺口。当前正文/冲突通过且历史未检查时，两种提示都进入检索/停止选择；明确 stop 与终止工具仍有效，不自动生成链接，不增加预算。
- 检索阶段只聚焦当前条目及已经交付的当前证据，排除同轮 peer 任务与无关缺失字段的锚定；这一替换不增加模型调用或来源。
- 无歧义的字段别名、完全重复的根级证据选择、空/可选 null 文档选择可无损整理，原始参数和整理动作都保存。不同 ID 列表、未指定归属的非空文档、缺失正向证据、缺失逐条关联证据仍拒绝。
- 不确定且未选择关系时，省略的空 links 可补成 []；不能给正向/已选择关系补造 links。格式错误反馈使用当前工具的确切键名，并明确没有支持时应保留 uncertain。
- focused 关系工具仅列出已读的旧记录 ID 和已交付的双方证据 ID；禁止把记录 ID 改造成新 link ID。continues/updates 与 changed_fields 的一致性在参数中明确表达，但不会自动更改模型所选关系。
- 无进展判定依据未解决字段与代码产生的校验状态，不受模型改写原因影响；真正的补证据/来源修正仍可继续。trace 保存 semantic_stalls 与 validation_codes。

P2_PROTOCOL_VERSION：14 → 19。80 次调用、100 万 token、300 秒请求超时、1200 秒整场超时、并发 4、重试 2、工具额度、终止条件和来源边界未提高。

最后的 v19 仅对完全一致的关系证据冗余字段去重。原 v18 实际失败响应在零 API 回放中恢复了目标 updates 关联；另两份原响应仍待确认。382 项测试通过。最新完整在线结果属于 v18，v19 尚未重新完整在线运行，不能宣称关联已稳定复现。详见 REPORT.md。

## 目录

- `before.json`：本轮开始时源码/测试与受保护文件哈希、前一轮产物清单。
- `fixed-v1/`：第一版修复（v15），完整两场回归及两次单条重复。记录到同版本同输入仍可出现链接/不链接；结果完整保留。
- `fixed-v2/`：v16，聚焦历史上下文、兼容可选空文档。主流程恢复关联，但独立重复暴露 link ID 和关系/变化字段提交不一致。
- `fixed-v3/`：v17，补齐已读 ID 与关系一致性的工具契约；暴露了同一未决关系只改原因措辞仍反复检查的问题。
- `fixed-v4/`：仅准备、未在线运行的进展判定草稿。字段单独判定过早停止真实范围更新回放，已由最终方案替代。
- `fixed-v5/`：最新完整在线 v18，保留代码校验状态的真正进展，并终止纯措辞改写的空转。该目录 `summary.json` 只代表 v18 在线结果。
- `final-response-replay-v3/`：当前 v19 源码与相同真实响应的零 API 回放，包含 JSON、报告、trace、澄清和 tests.json；不能当成新的在线抽样。前两份回放驱动错误发生在消费响应前，保留 INCOMPLETE.json，没有 API 调用。
- `stagnation_replay.json`：完全相同的旧停滞提交，9 次→8 次并以 no_progress 退出，保留待确认；真实 TV 范围更新回放仍通过。
- `REPORT.md`、`verification.json`、`manifest.json`：最终说明、完整性检查和 SHA-256 清单。

```powershell
python agent/results/p2_protocol_followup/run_study.py prepare agent/results/p2_protocol_followup/new-run
python agent/results/p2_protocol_followup/run_study.py run_all agent/results/p2_protocol_followup/new-run
python agent/results/p2_protocol_followup/analyze.py agent/results/p2_protocol_followup/new-run
python agent/results/p2_protocol_followup/replay.py
```

每次必须创建新目录。API 凭证只由环境提供。所有完整运行、重复诊断及模型停止都独立保留；没有运行 gold/F1 或最终 evaluation，没有新增人工确认。
