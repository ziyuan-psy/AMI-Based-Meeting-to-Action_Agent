# 精简版：你们要做什么

原则：能用仓库现成的就不重写；能用代码规则的不用模型；检索用最简单的方式；只保留能支撑老师四条意见的部分。

## 0. 仓库里已经有的（不用做）
- 24 场 TXT 转写（`transcripts/SB`、`transcripts/SC`）
- TXT→JSON parser 及测试
- 24 份 gold JSON 与 schema 模板、字段说明（README）
- 语料中的参与者信息（在 `dialogueActs` 的 `attributes` 里）、`amiScenario.pdf`

## 1. 必做（按顺序）

| # | 任务 | 具体做什么 | 产出 | 工作量 |
|---|---|---|---|---|
| 1 | **确认 gold 冻结状态** | 和标注同学确认 SB/SC 的 gold 是否已定稿；README 仍写"draft"，需统一说法 | 一句确认 + 冻结日期 | 很小 |
| 2 | **建参与者表** | 遍历 24 个 `dialogueActs/*.json`，取 `speaker → attributes.participant, role`，检查每场每个字母唯一 | `participants.json` | 小（~30 行脚本） |
| 3 | **建 Context Store（极简）** | ① 转写：每场 parse + 补参与者后存成 JSON；② 场景文档：把 `amiScenario.pdf` 转成文本按页切块；③ 前序系统输出：就是文件夹里的上一场结果。**检索只用关键词（BM25）**，不上向量库 | `store/` 目录 + 一个 `search()` 函数 | 小–中 |
| 4 | **写字段模板 prompt** | 把 V4 §3.2、§3.3 的字段表和规则直接放进 system prompt；输出用 JSON schema 约束 | `prompt_template.md` | 小 |
| 5 | **抽取（N2）** | 分块交给模型，输出候选条目（含证据 ID、owner/deadline 原话）；丢掉无真实证据 ID 的条目 | 候选 JSON | 中 |
| 6 | **规范化 + 分诊（N3/N4）** | 规则：说话人→ID、角色词→ID、"we/someone"→ambiguous、deadline 保留原话；flag 先做 4 个：`OWNER_UNRESOLVED`、`POSSIBLE_PRIOR`、`DEADLINE_CHECK`、`STATUS_CHECK` | 规则代码 | 中 |
| 7 | **Agent 循环（N5）** | 4 个工具足够：`search_dialogue`、`get_prior_items`、`get_participants`、`finish`（`read_dialogue`、`search_project_doc` 作为可选）；代码里写：白名单、单条目 ≤4 次、整场 ≤30 次、禁止重复调用、只能读同 series 且序号更小的会议；每步写 JSONL 日志 | agent 代码 + `trace.jsonl` | 中–大（核心） |
| 8 | **拼装 + 校验 + 修订一次（N7–N9）** | 用 schema 模板校验；补几条规则（basis 与值一致、引用 ID 存在、related 只指向更早）；失败让模型修一次，再失败标 BLOCKED | validator | 中 |
| 9 | **可读报告（N10）** | 模板渲染 Markdown：概要、决策、问题、action 表、与前序的变化、待确认事项 | `report.md` | 小 |
| 10 | **基线 B0 和消融 S1** | B0：同一份模板，一次调用，不检索；S1：同一系统，关闭前序检索（`scope=prior`、`get_prior_items` 返回空） | 两个开关 | 小 |
| 11 | **评估脚本** | 见下表 | 指标表 | 中 |
| 12 | **Demo** | 用 SB 的 TS3006 a→b（gold 里 `TS3006b.dharshi.s.12` updates `TS3006a.elana.s.9`），展示：旧记录、本场对话、agent 轨迹、S1 对比、最终 action 表 | 一个 notebook/页面 | 小–中 |
| 13 | **报告与讲稿** | 方法、实验、失败分析、局限 | 文档 | 中 |

## 2. 评估怎么做才"够用"

| 指标 | 做法 | 难度 |
|---|---|---|
| 条目对齐 | 同类型内按文本相似度 + 证据 ID 重合度做一对一匹配 | 中 |
| Action/decision F1 | 对齐后统计 | 小 |
| Owner 准确率 | 对齐的 action 上，owner ID 集合是否相同；`owner_basis` 是否一致 | 小 |
| Deadline 准确率 | 原话是否一致（先用规则/字符串，剩余用模型判断）；误造 deadline 率 | 小–中 |
| Status、`related_items` | 直接比枚举值与 (目标, relation) | 小 |
| **Evidence support** | LLM judge：给定条目 + 引用的对话原文，判断 supported / partial / unsupported（owner、deadline 各判一次）；**人工抽 30 条核对** | 中 |
| 有/无前序上下文 | S1 vs S2；单独看 gold 里 `prior_context_needed=true` 的条目 | 小 |

## 3. 可以砍掉的（不影响老师的四条意见）
- 向量检索 / embedding（BM25 够用）
- 固定顺序对照系统 S3
- `search_project_doc` 的复杂版本（只做简单页检索，甚至只在 prompt 中放场景摘要）
- 7 个 flag 缩成 4 个
- abstract 的评估（报告但不当主指标）
- 多 prompt 版本调参（SB 上调 2–3 轮即可）

## 4. 必须守住的（否则效果或可信度出问题）
1. **SC 冻结**：SB 上定完 prompt、阈值、agent 指令后才跑 SC，且只跑一次；SC 不用于 demo、调参、debug。
2. **按序处理同 series**：a→b→c→d；b 能读 a 的转写和系统输出，不能读 gold，不能读 c/d。
3. **工具限制写在代码里**，每步都有日志。
4. **报告 evidence support**，不能只报"引用存在率"。
5. **few-shot 只用 SB**；评估时排除被用作示例的条目。

## 5. 建议分工（4 人为例，按需调整）
- A：数据与检索（任务 2、3、10）
- B：抽取、规范化、分诊、字段模板 prompt（4、5、6）
- C：agent、校验、报告渲染（7、8、9）
- D：评估脚本、demo、报告（11、12、13）；并负责与标注同学确认 gold（1）

## 6. 建议顺序
1. 先走通**最小闭环**：TXT → parser → 抽取 → 校验 → 报告（不含 agent），在 SB 一场会议上跑通。
2. 加 B0 基线与评估脚本，先在 SB 上跑出一张指标表。
3. 加 Context Store 与 agent，看 S1/S2 差异。
4. 在 SB 上定参，冻结。
5. 跑 SC 一次，出最终表；做 demo；写报告。
