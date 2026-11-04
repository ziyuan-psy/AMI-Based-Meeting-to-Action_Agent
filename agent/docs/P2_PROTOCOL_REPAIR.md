# P2 证据协议、字段隔离和调用消耗修复

本次修复针对第八步真实回归发现的协议失败、过度隔离和 80 次调用耗尽。
生产预算仍为每场 80 次模型尝试、100 万 token、300 秒请求、1,200 秒整场和 4 路并发。
UI、主输出 schema 和原标注文件保持原样。

## Wire protocol v2

普通 `assess_item` 使用平铺字段：`status`、`related_items`、`statement`、`status_check`、
`conflict_check`、`relation_check`、`next_action`。Action 另有 owner/deadline 的值、basis 和相应检查。
不再要求模型同时维护 `fields`、`checks` 两层容器及重复的字段身份。

每个检查必须提交 `result`、`reason` 和 `evidence_da_ids`。肯定检查必须选择至少一个已实际提供的
当前对话 ID；不让模型重新抄写易出错的引文。代码把选定 ID 绑定到已交付原文，并继续核对来源和权限。
矛盾检查的 pass 可以沿用已选 statement 引文作为检查范围；这不把不确定/冲突判断改成 pass。
`conflict_check.affected_fields` 明确影响哪个字段。模型仍决定证据是否支持、是否冲突以及后续动作。

`assess_relation` 与 `assess_document_context` 保持简洁的聚焦格式，使用各自 root `quotes`。
历史关系仍须取得真实旧记录及前后证据；文档仍须明确选用和通过来源检查，不能把文档 ID 当对话引用。
旧格式只由显式 `p2_legacy_input` 回放适配器解码，生产客户端默认不接受旧 wrapper。
原始提交、规范化检查、ID 绑定、工具返回与实际终止原因均保留。

平铺 wire 格式沿用 v2；最终解码/路由策略为 `P2_PROTOCOL_VERSION=3`，记录于 metadata，使较早策略的已完成缓存失效。
策略 3 补齐三个真实残留问题：pass 的 conflict 未写 affected_fields 时补空集合；丢弃不参与判断的
advisory confidence（保留 raw input 与规范化记录）；read_dialogue 缺失 meeting_id 时从明确选定的
完整对话 ID 唯一推导，再执行原可见性校验。显式给错的 meeting_id 不被静默改写。
仅关联尚未解决时才把补查限为历史；owner/status 等本地字段也未解决时允许继续读取当前会议。

## 支持的核心陈述与辅助字段分开处理

当模型认为任务受支持、但 owner/deadline/status/关联尚未确认时，先清除对应未确认字段，再做最终字段验证。
辅助字段的不确定不再因一个无效值连带抹去已通过的核心陈述。原提议值仍在 trace 中。
真正不受支持的陈述、未读/未来引用、伪造引文、错误文档来源仍会被拦截。
若任务文本本身包含不受支持的约束，statement 仍须保留不确定；没有自动重写文本来追求匹配数。

模型提交 accept 但只有辅助字段未解决时，控制器保存可记录的陈述和结构化澄清，实际状态是 pending。
不默认再触发一串查 roster、重交检查的回合。模型明确选择 retrieve/search 时，仍可自主检索和再次搜索。

## 同一预算内的调用分配

P2 在已有整场 controller 上使用 admission view，不建立额外预算。
每次实际尝试，包括 transport 重试，都占原全局调用、token 和并发额度。
保留尚未开始条目的首次尝试，并预留 `P2_RESERVED_COMPLETION_CALLS=3` 次模型尝试给摘要阶段。
先完成的条目释放未使用的分配空间，其他 Agent 可继续使用剩余额度。

局部额度不足时保存当前检查、证据、待确认请求，终止原因为 `p2_call_allowance_exhausted`。
它不把未检查内容判成通过。真正的 API 失败/超时仍按整场失败处理，不能伪装成事实待确认。
摘要真实失败或耗尽其可用额度时仍返回明确失败；三次预留不承诺九次结构化/transport 重试都会成功。
token 和时间仍由原 controller 硬性限制。

## 验证范围

[最终固定输入捕获](../results/p2_repairs/repair-v3/) 使用第八步原抽取、合并缓存和相同 normalize 条目，
重跑真实 P2 与摘要。原上游的调用、计入 token 和耗时先从剩余预算中扣除，不因缓存命中给 P2 额外额度。
metadata 单列原始上游成本、新 API 用量和合计预算；这不是重新抽取的全流程计时。

首轮策略 2 的 [repair-v1](../results/p2_repairs/repair-v1/) 三场固定输入均完成。旧 TS3006b 失败运行没有导出的 normalize 副本，捕获已从其原始 checkpoint
核对输入一致性。成功 native pipeline 产物保持原样，收尾恢复过程记录在 `postprocessing_recovery.json`；
评分使用该次实际冻结源码，恢复没有新增 API 调用。后续工具已补齐失败运行的 checkpoint 路径回退和测试。

两场严格前后评分均为系列首场；TS3006b 的历史来自修复后的 TS3006a，不能把它解释为所有 Agent
上下文都完全相同的单项实验。完成与效率结果、两场固定输入的前后指标分别报告。

策略 2 的 [fresh-v1](../results/p2_repairs/fresh-v1/) 空缓存整场验证保留为独立样本；新抽取包含模型随机性，不能把其全部分数变化归因于 P2。
最后的三处边界补丁使用 [focused-v3](../results/p2_repairs/focused-v3/) 的真实旧响应有限回放及三条在线输入复核。
回放中的未读 A.10 引用和语义冲突仍保留待确认；不把格式修复等同于自动通过证据验证。
最终策略 3 再运行 repair-v3 的完整缓存流水线，以相同上游输入和计入成本核对三场结果。
评分继续采用相同开发 gold、同类型一对一三票匹配，保存支持判断与未匹配条目。
这些 gold 为开发草稿，匹配和支持评分尚未独立人工复核；没有使用留出集，也不是最终 evaluation。

```powershell
# 在仓库根目录，使用现有环境及 API 配置，PYTHONPATH=agent/src
python agent/scripts/p2_repair_study.py prepare agent/results/p2_repairs/new-controlled
python agent/scripts/p2_repair_study.py run agent/results/p2_repairs/new-controlled
python agent/scripts/p2_repair_study.py score agent/results/p2_repairs/new-controlled

python agent/scripts/p2_repair_study.py prepare agent/results/p2_repairs/new-fresh --fresh
python agent/scripts/p2_repair_study.py run agent/results/p2_repairs/new-fresh
python agent/scripts/p2_repair_study.py score agent/results/p2_repairs/new-fresh
```

目标目录必须是新的，原有捕获不覆盖。协议/字段隔离、伪造和未读证据、首次调用保留、真实重试计数、
摘要余量、缓存上游成本及失败 checkpoint 回退均有离线测试；旧跨会议与文档真实响应回放继续保留。
