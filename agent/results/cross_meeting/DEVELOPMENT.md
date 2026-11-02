# 开发尝试记录

所有尝试原样保留。COMPLETED 指技术执行／保存完成；accepted/pending 是条目检查状态。
下表混有不同的调试代码版本，不能作为最终协议的成功率分母。
`link-v12`～`link-v14` 是同一较早协议的三次重复，均 pending；随后改为证据 ID 输入，
减少引文复制和无关字段负担，最终协议的在线正例为 `link-v15`，对照为 `missing-history-v2`。
模型决定查询、证据、关系和继续／停止；没有向它提供正确关系。每次源码与配置均可按哈希复查。

| 尝试 | 执行类型 | 条目／技术结果 | 退出原因 | 请求尝试 | 已知 token |
| --- | --- | --- | --- | ---: | ---: |
| [link-v1](link-v1/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 4 | 16,421 |
| [link-v2](link-v2/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 7 | 36,644 |
| [link-v3](link-v3/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 6 | 31,341 |
| [link-v4](link-v4/run.json) | live_cross_meeting_agent_stage | pending | invalid_action_limit | 2 | 9,098 |
| [link-v5](link-v5/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 7 | 43,232 |
| [link-v6](link-v6/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 9 | 81,814 |
| [link-v7](link-v7/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 5 | 36,399 |
| [link-v8](link-v8/run.json) | live_cross_meeting_agent_stage | FAILED | model_request_failed | 3 | 0 |
| [link-v9](link-v9/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 7 | 51,600 |
| [link-v10](link-v10/run.json) | live_cross_meeting_agent_stage | pending | unresolved_checks | 8 | 62,181 |
| [link-v11](link-v11/run.json) | live_cross_meeting_agent_stage | pending | unresolved_checks | 1 | 9,288 |
| [link-v12](link-v12/run.json) | live_cross_meeting_agent_stage | pending | invalid_action_limit | 6 | 53,980 |
| [link-v13](link-v13/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 7 | 51,332 |
| [link-v14](link-v14/run.json) | live_cross_meeting_agent_stage | pending | lookup_limit | 11 | 113,463 |
| [link-v15](link-v15/run.json) | live_cross_meeting_agent_stage | accepted | all_required_checks_passed | 5 | 36,915 |
| [missing-history-v1](missing-history-v1/run.json) | live_cross_meeting_agent_stage | pending | no_progress | 4 | 28,691 |
| [missing-history-v2](missing-history-v2/run.json) | live_cross_meeting_agent_stage | pending | unresolved_checks | 2 | 13,081 |
| [replay-v10-v1](replay-v10-v1/run.json) | cached_live_tool_response_replay | accepted | cached replay | 0 | 0 |

`link-v8` 是 Portkey 连续 HTTP 500：3 次尝试均失败，用量未知，统一预算保守扣除 151,740 token。
这不是已知实际用量，也没有作为语义检查成功。其 checkpoint 和失败原因均保留。
回放是零 API 的固定响应回放，不计作新的在线成功。

早期失败定位到：用会议 ID 搜索正文、重复检索、改写／漏填引文、把字段放错层级，
以及把 Decision 的 owner/deadline 当作待确认字段。
最终协议由模型选择已读证据 ID、代码绑定原文；已通过的本地检查不再重复提交，
历史比较使用独立 assess_relation，但保留改变查询和待确认停止。

189 项测试通过，另有 11 个 subtest。测试覆盖读取范围、旧记录快照、逐关联证据、
未读／错配证据拒绝、澄清和停止、预算，以及真实正例／对照的无 API 回放。
输出还在最终更严格的 witness schema 下重新校验；这不修改已捕获的运行快照。
人工确认只覆盖 CASE.md 中那条范围更新，并不把所有调试运行都标成人工通过。
