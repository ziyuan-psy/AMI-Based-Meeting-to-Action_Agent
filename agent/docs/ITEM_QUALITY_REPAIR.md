# 条目保留、Decision 粒度与 P2 有限修正

当前开发策略：`ITEM_POLICY_VERSION=5`、`P2_PROTOCOL_VERSION=6`。
沿用每场 80 次模型尝试、100 万 token、300 秒请求、1,200 秒整场、4 路并发和原重试限制。
UI、主输出 schema 与原标注文件保持原样。本次变更尚未提交。

## 条目保留

恢复 Action 只检查其他 Action 是否覆盖该任务；不同 owner 不因证据重合而互相吸收。
Decision 替换完成后再次恢复 Action，避免先按 Decision 吸收、后又删除而丢失。
启用选择阶段时，各合并样本和恢复过程不提前截断。旧缓存有原始 groups 时重新解释真实
响应，保留旧响应与来源；缺少原始响应的缓存不会假称恢复了不可见条目。

新增 `select` 阶段由同一模型提出：独立事实的拆分、同任务去重、相关性排除及项目优先级。
输入含候选、真实 owner ID 和候选引用的当前原文。每个候选使用固定 C-key：输出必须在
同一 key 下提交 keep、claims、reason；重复项可指向另一个被保留的同类型 key。
代码按 key 绑定已交付源证据，不允许模型在排序后猜测来源编号；不同 owner 的 Action
不能作为重复项合并。一个 Decision 来源可拆成多个独立 claims，类型不任意改写。
缺失 key、未交付证据和没有明确处置的候选都会拒绝。不安全的语义重复建议保留原候选
交给 P2；Decision 的信息若明确重复于已有 Action，可排除重复信息，但不会跨类型制造
新的 Action 或绑定错误来源。每次原始提交及
规范化后的成员对应关系都记录，不能因格式错误静默丢弃候选。

明确的介绍/破冰活动本身由代码排除，产品引入、客户发布等实质项目内容不受该规则影响。
非 Decision 不因模型把同一问题拆成多句而扩张记录。保留/重复建议矛盾时原候选仍须交给 P2。
最后先覆盖不同明确负责人的实质 Action，再按项目优先级、时间信息和稳定次序应用原 5/7/4 上限。
恢复条目与其他条目参加同一次排序，不因追加在末尾而必然截断。最终选择仍可能不理想，
所有被截条目、rank、limit 和理由保留在 `select/cap` trace 中；不保证找回每个 gold 条目。
P2 独立核验选择后的陈述及证据，`select` 不等于支持检查通过。

## Decision 粒度与相关性

选择阶段明确区分独立价格、销量目标、成本约束、范围、功能和布局选择；同一个候选中的
不同事实可拆成独立条目。介绍、破冰、练习及发言顺序本身不作为项目 Decision。
真实阻碍和已解决问题仍可保留；不能仅因 gold 没标注而删去一个有证据的项目问题。
这些是通用任务规则，不读取 gold、队友答案或留出集。

## P2 的有限修正

Agent 可在 statement 尚不确定/失败时选择 `next_action=revise_statement`，也可继续自主
检索、搜索或停止。`revise_statement` 只允许一次删除原句末尾的时间/方法等限定：

- 必须选择已经交付的当前会议证据。
- 必须是原句的逐字尾部，保留至少一半单词和最小核心句。
- 不允许删除否定、条件、情态、批准/许可前提，也不能更换 actor、任务或对象，不能添加词句。
- 不允许用文本修正绕过尚未完成的显式文档上下文检查。

修正后撤销原所有检查、字段与关系结论，必须重新 `assess_item`。修正本身不判 pass、
不增加预算，也不重置查找额度。新文本、旧文本、删除内容、理由、所选证据和重新检查
都写入 trace。主输出和 sidecar 必须指向同一重新检查过的文本；未重新检查不能发布。
需要改 actor、补核心承诺或重写条件的条目仍保留待确认。

明确星期或 ISO 日期必须出现在所选当前对话中；模型说 pass 也不能凭空引入 Friday 等日期。
这只是必要的来源锚定校验，不是通用语义蕴含证明；相对时间和其他限定仍需模型检查。
相同真实错误提交的零 API 回放见 `time_guard_replay.json`。错误注入线上样本和回放均不计入 F1。

现代协议在有真实已读引用、但核心陈述被语义不确定或真实冲突检查拦下时，可在最终
待确认之前提供一次 `assess_core` 聚焦复核。模型可复核 statement/conflict，也可 stop/clarify；
不会自动判 pass，缺失字段不必连带抹去任务。未读引用、伪造引文、文档缺口和日期来源
失败不能走这条复核来绕过检查。该模型尝试同样受原调用、token、步骤和时间限制。
显式 v1 回放适配器保留历史协议；生产客户端使用新协议。

## 开发回归与冻结

`agent/results/quality_repairs/retention-v1` 保存首轮失败：IS1008a 完成，TS3006a 在
选择阶段因结构响应错误明确失败并停止后续会议；没有将其记为完整评估。
`retention-v2` 使用简化协议另起运行，因 IS1008a 返回错误成员编号而停止；
`retention-v3` 使用固定键，但合理的 Decision/Action 重复排除被过严校验拒绝；
`retention-v4` 在 parse 阶段出现 PermissionError，新增 API 为 0，文件随后可读。
`final-v5` 的前两场完成，第三场被矛盾的保留/重复建议拦下。`release-candidate` 仅准备和
离线预检，没有 API 运行。`release-v6` 在第三场保存产物时出现 PermissionError；
`release-v7` 补齐 Windows 短时原子替换重试后三场均完成。`release-v8` 只用于额外日期校验
的冻结与定向测试，没有完整自然案例运行。最终 `release-v9` 加入一次聚焦复核，
从同一冻结输入重新运行三场完整 P2/摘要，不拼接不同轮次的成功输出。
选择阶段固定复用 final-v5 每场第一次真实模型响应；核对相同请求、模型、system 和 wire，
并计入该请求的实际调用、token 与耗时，不因缓存获得免费额度。不是依据评分挑选响应。
各轮输入相同，失败捕获不覆盖，也不从失败轮挑选评分。

固定的三场为 IS1008a、TS3006a、TS3006b，before 是 P2 修复策略 3 的真实产物。
抽取候选与合并原始响应被冻结；原上游调用/token/耗时先扣除，再真实运行选择、P2 和
摘要。上游样本不重新采样，不能把缓存耗时解释成空缓存整场加速。
TS3006b 使用本轮 TS3006a 的历史记录，历史上下文随修复变化。

评分单独读取相同开发 gold，使用同类型、一对一、三票多数及相同 few-shot 排除口径；
before 与队友分数直接复用冻结判断，不重复抽取参考分数。输出同时比较进入 P2 前与
最终结果，以区分上游漏项、P2 隔离和未匹配条目。模型评分和开发草稿不能代替独立评估。

在仓库根目录设置现有依赖与 API 环境后：

```powershell
$env:PYTHONPATH='agent/src;agent/logs/trace_step_deps'
python agent/scripts/quality_study.py prepare agent/results/quality_repairs/new-run
python agent/scripts/quality_study.py run agent/results/quality_repairs/new-run
python agent/scripts/quality_study.py score agent/results/quality_repairs/new-run
```

命令拒绝覆盖已有运行，运行/评分前校验输入和当前源代码哈希。输入、源码、原始请求、
阶段 checkpoint、P2/澄清 sidecar 与评分各自保存。源码变化须新建捕获目录。
关键案例需要人类核对；在核对回复记录之前，只能标为待人工核对，不能宣布冻结通过。
