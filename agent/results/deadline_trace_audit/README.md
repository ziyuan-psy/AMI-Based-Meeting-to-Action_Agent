# Slides 时间定位与 TS3006b 调用审计

本轮基于 `agent-development@3b886f6` 的现有未提交修复继续开发，未 commit。UI 和标注文档保持原样。

## 已定位的问题

- IS1008a 的 slides 时间从初始抽取起就是 `null/missing`，合并、选择没有再删除一个已有时间。P2 已读到请求中的 “at the conclusion of our meeting”，却只用 “Yes, I will” 检查 deadline，随后直接澄清。这是语义漏读，不是字段只能保存日历日期。
- TS3006b 共 74 次调用，包含 22 次上游调用、51 次 P2/Agent 调用、1 次摘要。重复执行的历史查询有 3 次；“TS3006a decisions”没有内容关键词，也曾实际消耗一次检索额度。两次纯会议编号查询已被旧版拒绝，但仍消耗模型提交。
- TS3006b 约 635.683/797.3 秒（79.73%）已发生在抽取、合并和选择结束前。因此清理 P2 空转不能被表述为解决了全部延时。
- 核心复查重复要求一个已经通过的 conflict 结果，引起两次缺字段重试。现在仅对已通过、同正文、同来源范围的 conflict 保留旧结果；未解决的冲突仍必须重新提交。

## 针对性修复

1. 明确保留事件相对的交付时间，并把请求和承诺回应一起检查。仅在核心已支持、唯一缺口为 missing deadline，且当前正文证据或该候选已交付的原始证据包含时间线索时，提供一次 deadline 专项复查；不自动填写，不新增来源，受原预算约束。明确 stop 及终止/澄清工具仍保留。
2. 工具菜单列出已执行参数组合；代码拒绝同条目中的完全重复/关键词等价调用，不再消耗额外检索额度。改变范围、类型、页或窗口仍可使用。当前网关不支持 schema 顶层逻辑组合，约束由控制器执行。
3. 会议/记录编号和条目类型标签不能单独作为历史正文搜索词。空搜索后可由 Agent 改查、读取、提交关系检查或停止；不强迫重交完整本地检查，也不自动认定“没有历史关系”。已解决 owner 时不再提供无关参与者查询。
4. 核心专项复查可以保留已经通过的 conflict；复查仍不确定且未给下一步提示时，保持待确认。明确提出继续检索的选择仍可执行。
5. 在线试跑还暴露无历史作用域下错误选择同轮关联并省略 relation_check 的 KeyError，现改为可纠正的协议错误，不再索引缺失检查。

P2_PROTOCOL_VERSION：9 → 14。Action 上限、调用/token/时间/并发/重试上限保持原值。

## 验证与产物

- `before.json`：本轮开始时的源文件/测试哈希、原试跑清单与 UI/标注文档哈希。
- `before_audit.json`：原 slides 各阶段和 TS3006b 逐条实际行为。
- `before_cost_audit.json`：原调用/时间分布、重复查找和错误提交。
- `captured_replay.json`：完全相同旧模型参数的离线前后重放，无 API；两处检索计数 4→2、3→2，一处核心复查提交 3→2。后者仍保持待确认，没有制造正向结论。
- `fixed-v5/`：同一上游候选、TXT、参与者、scenario 和 TS3006a 历史记录的在线 P2 回归。原上游调用、token 和时间预留，从剩余预算开始；P2、摘要、验证和报告新执行。**这不是空缓存全阶段试跑，也没有运行 gold/F1 evaluation。**
- `fixed-v1/`：沙箱连接失败的原记录；unknown token 仅为控制器的保守预留，不代表实际 API 用量。
- `fixed-v2/`：暴露缺少关系检查 KeyError 的失败记录，后续会议未运行。
- `fixed-v3/`：暴露新增顶层 allOf 与 Portkey 不兼容的失败记录，后续会议未运行。
- `fixed-v4/`：继续暴露 slides 只选择了回应、未选择原请求时无法触发时间复查的问题；原始输出保留，不作为 slides 修复成功结果。
- `http400_diagnostic.json`：一次、零重试的脱敏复现，确认网关拒绝顶层 allOf。凭证未保存或输出。

固定输入回归使用：

```powershell
python agent/results/deadline_trace_audit/regress.py prepare agent/results/deadline_trace_audit/new-run
python agent/results/deadline_trace_audit/regress.py run agent/results/deadline_trace_audit/new-run
python agent/results/deadline_trace_audit/regress.py extract_probe agent/results/deadline_trace_audit/new-run
python agent/results/deadline_trace_audit/audit_results.py after agent/results/deadline_trace_audit/new-run
```

始终使用新目录；原始运行、失败和实际提交均保留。独立 extraction probe 不用于填补主回归结果。单次真实模型前后输出仍包含模型波动，调用差异不能等同于稳定的速度收益。
