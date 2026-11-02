# 跨会议开发案例：TS3006a → TS3006b

本例已完成真实在线 Agent 运行，并由本聊天中的用户人工确认 `updates`（范围更新）正确。
[人工确认原文](human_review.json)及[与在线产物的对应关系](human_review_links.json)独立保存；不改写原始运行记录。

## 旧记录 → 检索证据 → Agent 选择 → 新记录

| 环节 | 实际运行内容 | 可复查产物 |
| --- | --- | --- |
| 旧记录 | `TS3006a.sys.s.6`：包含先以 TV 和 video 为范围、保留扩展可能性的记录。 | [完整旧记录 JSON](link-v15/prior_records/TS3006a.json) |
| Agent 检索 | 自行选择 `get_prior_items(query="one device television", item_type="decisions")`，返回旧条目。 | [逐步 trace，第 2 步](link-v15/agent.trace.jsonl) |
| 核验证据 | 自行选择 `read_prior_item(item_id="TS3006a.sys.s.6", query="TV and video")`，读取实际旧原文及上下文。 | [逐步 trace，第 4 步](link-v15/agent.trace.jsonl) |
| Agent 判断 | `assess_relation` 选择 `updates`、`changed_fields=["scope"]`，指出范围由 TV＋video 缩小到仅 TV。 | [检查、前后快照和引文](link-v15/TS3006b.p2.json) |
| 新记录 | `TS3006b.sys.s.1`，`status=adopted`，关联旧条目为 `updates`，附两次会议的原文引用；旧 JSON 不变。 | [新记录 JSON](link-v15/TS3006b.json) |

关键原文：

- 旧会议 `TS3006a.A.dialog-act.vkaraisk.243`：
  “We could just start with the assumption that's only for T_V_ and video .”
- 旧会议 `.244`：
  “And um reserve the possibility to add other features .”
- 当前会议 `TS3006b.D.dialog-act.dharshi.132`：
  “Yeah but uh the the newsflash also stated that it should control only one device , only your television .”
- 当前会议 `TS3006b.B.dialog-act.dharshi.68`：
  “Okay one device .”

本例只核对**设备控制范围**：从初始范围假设更新为明确的仅 TV 要求。
旧记录还包含“三阶段流程”等内容；本例不声称三阶段流程被取消，也不声称双设备功能曾经实现。

用户的人工确认：**“确认，updates（范围更新）正确”**。
其范围是上述变化与关系，不表示所有抽取结果或整场会议已经通过人工评估。

## 最终协议的真实运行

| 场景 | 结果 | 模型调用 | 实际 token | lookup |
| --- | --- | ---: | ---: | ---: |
| 完整历史记录 `link-v15` | accepted；新记录包含证据支持的 updates | 5 | 36,915 | 2 |
| 缺历史记录 `missing-history-v2` | pending；保留当前陈述，关联为空并产生澄清请求 | 2 | 13,081 | 0 |

两次生成使用相同运行代码、模型、提示和配置。缺记录是受控消融：仍允许读同系列早期对话，
但没有旧系统记录可关联。Agent 自行选择停止，代码没有虚构旧 ID。
[缺记录结果](missing-history-v2/TS3006b.p2.json)、[澄清请求](missing-history-v2/TS3006b.clarifications.json)。

模型为 `@30875-fall26-openai/gpt-5-nano`，跨会议比较使用 `EFFORT_LINK=medium`。
完整历史案例约 113.7 秒，缺记录对照约 33.1 秒（包含运行记录和源码快照准备）。
每次均使用原有每场 100 万 token、80 次请求尝试、请求 300 秒、整场 1,200 秒及工具调用限额。
[正例运行配置与用量](link-v15/run.json)、[对照配置与用量](missing-history-v2/run.json)。

## 运行与复现范围

这是生产 P2 Agent、真实工具、统一预算、记录组装和校验器的聚焦运行。
当前输入来自真实保存的 MVP `TS3006b.sys.s.11`，历史是完整保存的 TS3006a 开发基线，
没有手写历史条目，也没有给 Agent 预置目标旧 ID、正确关系或搜索查询。
没有重跑抽取和摘要，没有读 gold、没有调用 judge，也没有运行最终 evaluation。

同一真实模型响应的回放保留在 [replay-v10-v1](replay-v10-v1/run.json)，用于解释证据绑定修复。
它是**固定真实响应的离线回放**，新增 API 调用为 0，不能与 `link-v15` 的在线实跑混称。
回放中的原始模型已选择 updates；当时因引文遗漏而 pending，原始失败记录保持不变。

所有在线调试尝试都保留，见[开发尝试记录](DEVELOPMENT.md)。早期版本出现协议错误、无进展停止和
一次 API 500 失败；最终协议只完成这里的一组正例／对照，不能据此估计总体正确率或稳定性。
更广泛的跨系列、关系类型和整场效果由后续独立评估验证。

复现命令（在 agent 目录，配置现有依赖及 API 环境）：

```powershell
$env:PYTHONPATH = 'src'
python scripts/link_demo.py results/cross_meeting/<new-output-folder>
python scripts/link_demo.py results/cross_meeting/<another-new-output-folder> --without-records
```

所有输入、源码、schema、模型与配置、实际工具返回、决策理由、停止原因和产物哈希均保存在对应目录。
生成时的 review 标志维持 pending；后续实际人工确认以独立的 human_review 文件为准。
