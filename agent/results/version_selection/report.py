"""Concrete component decisions, with coverage and integration limitations stated explicitly."""
from __future__ import annotations
import json
from study import ROOT,REPO,load,save,utc

LABELS={'baseline':'414a31f 基准','retention_actions':'类型恢复＋新排序',
        'retention_actions_issues':'再加入 Issue 自动补回','selection213':'213ccfc 选择层',
        'selection_issue':'当前 Issue 版选择层','p2_current':'当前 P2 硬门禁'}
def scores(x):return ' / '.join(f"{x[k]['f1']:.3f}" for k in ('actions','decisions','problems','pooled'))
def main():
    v=load(ROOT/'verification.json');assert v['status']=='PASSED'
    c=load(ROOT/'comparison.json');s=load(ROOT/'stable_retention/comparison.json');a=load(ROOT/'content_and_alignment.json')
    cost_rows=[]
    for mid in load(ROOT/'study.json')['meetings']:
        current=load(ROOT/'boundaries'/mid/'p2_current/result.json')
        original=load(REPO/'agent/results/dev16_comparison/213ccfc-v1/baseline/attempts'/mid/'result.json')
        cost_rows.append({'meeting_id':mid,'p2_status':current['status'],'p2_api_attempts':current['new_api_attempts'],
                         'legacy_agent_api_attempts':sum(i['stage']=='agent' for i in original['runtime_control']['attempts'])})
    paired_cost=[r for r in cost_rows if r['p2_status']=='COMPLETED']
    cost={'paired_meetings':len(paired_cost),'p2_attempts':sum(r['p2_api_attempts'] for r in paired_cost),
          'legacy_agent_attempts':sum(r['legacy_agent_api_attempts'] for r in paired_cost),'per_meeting':cost_rows,
          'note':'Same fixed candidate/history boundary. P2 checks all items; legacy only flagged items. Costs reflect different verification coverage, not identical capabilities.'}
    save(ROOT/'call_costs.json',cost)
    engineering=['统一 TXT parser 与兼容入口','整场调用/token、请求与整场超时、重试/并发控制和失败产物保存',
      '候选/合并样本/投票/截断/工具返回/source/model/config trace',
      'members 类型和选择响应重复元数据等安全的格式校验修复','JSON schema、来源存在性及系列/时间读取限制']
    decisions=[
      {'component':'parser_budget_trace','decision':'KEEP','details':engineering},
      {'component':'action_type_aware_recovery','decision':'KEEP_WITH_414_STABLE_ORDER',
       'details':'保留现代 restore_actions 的同类型/owner 约束恢复；采用 414 原有顺序及原 5/7/4 上限。固定响应16场仅补回TS3006a两条真实任务，没有删除旧条目。'},
      {'component':'new_ranking','decision':'RESTORE_414_STABLE_ORDER',
       'details':'新排序的Action F1为0.520，高于稳定排序0.505；但新增匹配中的两条正是相同的TS3006a恢复，额外一个TP来自未改文本的alignment变化，并且IS1008d会中传递原型操作挤掉旧池条目。选择最小、已核对的内容变化；不宣称新排序整体无益。'},
      {'component':'issue_automatic_restoration','decision':'RESTORE_414_DEFAULT',
       'details':'Issue补回确实使F1 0.429→0.457、recall 0.405→0.568，但precision 0.455→0.382；净增22预测、6TP和16未匹配预测。建议作为复核候选池保留，暂不作为默认直接发布策略。这是保守精度取舍，不是没有收益的结论。'},
      {'component':'content_prompts_and_decision_second_pass','decision':'PREFER_414_UNTIL_PROVEN',
       'details':'内容默认回到414抽取/常规合并/投票口径。新抽取提示、Decision专项重合并和细粒度改写尚无逐项可归因的稳定整体收益证据。本轮没有把历史新采样差异说成提示词单独造成的效果。'},
      {'component':'additional_model_selector','decision':'RESTORE_BASELINE_NO_ADDED_SELECTOR',
       'details':'不把新增强制模型选择/改写设为质量候选默认。Action/Issue有收益，但两个选择臂的Decision F1分别从成对基线0.448/0.440降至0.374/0.397。输出重写也改变整组matcher。格式健壮性代码保留。'},
      {'component':'p2_semantic_publication_gate','decision':'RESTORE_FLAG_ONLY_FOR_QUALITY_CANDIDATE',
       'details':'质量候选恢复 flag-only Agent 跟进；完整 P2 硬门禁保留在现有开发/实验实现中。ES2011b两条已读到明确支持的任务/决策仍被隔离，不能设为质量默认。不得把未知检查改成pass，也不得放松strict P2来源验证。'},
      {'component':'retrieval_document_checks_clarifications','decision':'PRESERVE_IMPLEMENTATION_AND_EVIDENCE',
       'details':'保留自主检索、同系列更早会议限制、read_project_doc、文档证据校验/引用和结构化澄清实现及案例。恢复默认路径会降低全量P2检查覆盖面，不能宣称所选质量候选已同时验证全部P2功能。'},
    ]
    save(ROOT/'decisions.json',{'created_at':utc(),'status':'COMPONENT_SELECTION_COMPLETED_NOT_APPLIED_TO_DEFAULT_PIPELINE',
       'quality_anchor':'414a31fe1581b147851ec2bd61b340bf0be26843','working_branch':'agent-development',
       'production_source_changed_this_task':False,'existing_uncommitted_changes_preserved':True,
       'selected_quality_candidate':'414 content policy plus type-aware Action restoration with 414 stable order, current parser/budget/trace infrastructure, legacy flagged follow-up',
       'raw_metric_maximum_arm':'retention_actions_issues (pooled F1 0.469); retained as research option, not selected for direct publication due to Issue precision tradeoff and ranking source counterexample',
       'decisions':decisions,'integration_full_pipeline_test_completed':False,
       'requires_on_application':['按函数恢复策略，保留现代transport/controller/observation，不整文件checkout旧pipeline',
          '显式记录所选有效策略及source/prompt哈希，不能让配置仍显示启用而隐式跳过',
          '补全恢复任务的实际支持引用，尤其TS3006a的ME任务句151',
          '组合版本需独立集成检查；不把单层回放F1称为最终在线分数']})
    lines=['# agent-development 版本取舍', '',
      '本轮完成固定输入的组件取舍，**没有覆盖现有开发源码，也没有切换默认运行策略**。未 commit、未 push。', '',
      '**建议质量候选：以 414a31f 的内容策略为主体，单独保留 Action 类型恢复，使用原有排序；继续使用现代 parser、预算和 trace。新增选择层与完整 P2 硬门禁暂不进入质量默认。**', '',
      '这是组件恢复清单，不是已经完成整合和最终 evaluation 的交付版本。现有 P2/检索/文档验证/澄清实现和案例全部保留。', '',
      '## 最明确的保留证据', '',
      '| 同一份414真实模型响应，16场 | Action F1 | Decision F1 | Issue F1 | 合计微平均F1 |',
      '|---|---:|---:|---:|---:|',
      '| 414a31f | '+ ' | '.join(f"{c['aggregate']['baseline'][k]['f1']:.3f}" for k in ('actions','decisions','problems','pooled'))+' |',
      '| 只保留 Action 类型恢复＋414原排序 | '+' | '.join(f"{s['aggregate'][k]['f1']:.3f}" for k in ('actions','decisions','problems','pooled'))+' |', '',
      '第二行是固定响应的确定性回放，生成调用为0，仅新增3次评分调用。只新增TS3006a的UI技术功能任务和ME用户需求任务，未删旧条目；两条均核对了原文。ME原候选引用没包含任务句151，应用组合时必须补齐支持引用。本表不代表新组合的空缓存全流程分数。', '',
      '## 其他独立边界对照', '',
      '| 对照臂 | 有评分会议 | Action / Decision / Issue / 合计F1 | 同批会议基线合计F1 |',
      '|---|---:|---|---:|']
    for arm in ('retention_actions','retention_actions_issues','selection213','selection_issue','p2_current'):
        p=c['paired'][arm];lines.append(f"| {LABELS[arm]} | {len(p['meetings'])}/16 | {scores(p['arm'])} | {p['baseline_on_same_meetings']['pooled']['f1']:.3f} |")
    lines += ['', '每一行只与同一行的成对基线比较；未评分/生成失败会议不记零分。类型内 NA 类别不计分，排除原来的10条few-shot gold及对应匹配，评分模型、prompt、schema和三次多数票保持不变。', '',
      '**没有把最高一次F1当作唯一取舍标准。** 新排序＋Issue补回的合计F1 0.469最高，高于建议最小恢复版0.459；Issue的P/R/F1分别从0.455/0.405/0.429变为0.382/0.568/0.457。它净增加22条预测、6个匹配和16条未匹配预测。建议保留扩展候选作复核，不作为默认直接发布；这不是“改动没有作用”。', '',
      '新排序的Action F1为0.520，稳定排序为0.505；相对基线新增的两个新文本匹配正是同样的TS3006a任务，另外一个匹配来自未改文本的alignment变化。新排序还引入会中传递原型操作。建议选择内容变化最小、没有替换旧条目的恢复方式；现有排序实现继续保留供实验。', '',
      '边界输入：抽取后处理固定414的候选和三次合并响应；选择层固定同一份414 normalized候选和实际对话；P2固定同一份候选、414 flags和历史记录。历史只能读取同系列更早会议。每个运行臂从原预算中扣除414原抽取/合并的实际调用、token和耗时。', '',
      '## 抽取证据的适用范围', '',
      'extraction_audit.json 还复用了414、213及当前Issue版的16场真实候选，逐场确认TXT、模型和配置相同。候选总数（Action/Decision/Issue）：','']
    for name,vals in load(ROOT/'extraction_audit.json')['candidate_counts'].items():lines.append(f"- {name}: {vals['actions']} / {vals['decisions']} / {vals['problems']}")
    lines += ['', '共享gold对话行的统计仅用于定位上游是否触及相关段落，不是语义召回率或F1。不同提示词的历史响应仍含模型采样差异；本轮能明确归因的是同响应的恢复/排序变化，不能声称已证明每一条新提示词比旧提示词差。证据不足的内容策略按事先规则优先恢复414。', '',
      '## 取舍清单', '', '| 部分 | 取舍 |', '|---|---|',
      '| parser、预算、超时、重试/并发、失败保存、trace | 保留 |',
      '| Action 类型转换后的恢复 | 保留；与新排序拆开 |',
      '| 新排序、Issue 自动补回 | 恢复414稳定策略 |',
      '| 新抽取提示、Decision专项重合并及细粒度改写 | 未证明独立稳定收益，质量默认优先414 |',
      '| 新增模型选择层 | 质量默认恢复无额外选择层 |',
      '| 完整P2语义硬门禁 | 保留开发实现；质量候选恢复flag-only跟进 |',
      '| 来源/结构校验、格式修复、检索、文档引用、澄清实现与案例 | 保留，不把未知检查改成pass |', '',
      '完整P2硬门禁仍未达到质量默认的证据标准。恢复flag-only会降低默认检查覆盖面，交付说明须明确这一点；当前源码未切换，完整P2功能仍在。按函数级恢复内容策略可以保留现代工程能力，整文件回退旧pipeline会带走预算和trace，不能这样实施。', '',
      '## 原文核对与评分边界', '',
      '- ES2011b#4：明确要求所有人完成问卷并带新想法，P2却以owner/deadline/验收标准未齐全隔离整条任务。',
      '- ES2011b#11：statement/status已读到“we definitely want a power button and numbers”并判pass，conflict仍拿先前备选机制讨论阻止发布。',
      '- ES2011a：旧matcher把PM任务匹配给ID的gold；原句先点名Industrial Designer。同一PM文本仍在P2输出时，新组matcher又不给匹配。这不是P2删除了这条任务。',
      '- 当前matcher只接收每类条目的文本列表，缓存按整组文本生成key；其他条目或排序变化可能改变未改动文本的匹配。content_and_alignment.json 将这些变化与直接删除/改写分开。', '',
      'developer_case_review.json 是开发者原文核对，不是组员最终人工evaluation；gold和评分程序未修改。不能把旧TP全部视为原文正确，也不能把所有F1损失归因于P2。', '',
      f"同候选的{cost['paired_meetings']}场成功P2运行共用{cost['p2_attempts']}次API尝试；原flag-only Agent为{cost['legacy_agent_attempts']}次。P2检查所有条目，旧Agent只检查flagged条目；这是检查覆盖和消耗的取舍，不能说两种能力相同。", '',
      '## 完成情况与保存', '',
      f"- 固定16场，独立边界试验48次，成功 {v['boundary_trials_completed']} 次；失败保留：{json.dumps(v['generation_failures'],ensure_ascii=False)}。",
      f"- 评分覆盖：{json.dumps(v['score_coverage'],ensure_ascii=False)}。评分中断/预算退出：{json.dumps(v['scoring_partial'],ensure_ascii=False)}。",
      '- 初始串行评分切到原4并发时，IS1008a一个已准入请求未取得用量，按完整预留计入。原评分文件、成功投票、失败尝试及各次恢复目录均保留。IS1008a恢复计时包含暂停等待，保守触发评分时间上限；未据此给模型输出记零分，也未放宽预算补抽。失败记录里的stage=parse是该评分控制器的默认标签，实际发生于评分恢复等待，与TXT parser执行无关。',
      f"- 新生成API尝试 {v['actual_new_generation_calls']} 次；预算记账token {v['actual_new_generation_tokens']:,}。新增评分尝试 {v['actual_new_scoring_calls']} 次，记账token {v['actual_new_scoring_tokens']:,}。未知用量按完整预留记账，不能等同实际账单。",
      f"- {v['offline_score_views_replayed']} 个评分视图已从实际保存投票零API回放。JSON {v['json_parsed']}、JSONL {v['jsonl_parsed']} 全部解析通过。",
      f"- {v['prior_artifacts_unchanged']} 份旧实验产物、{v['protected_files_unchanged']} 份UI/标注保护文件、16份开发gold、{v['existing_uncommitted_files_unchanged']} 份已有未提交文件哈希不变。留出集未读取，密钥未写入产物。", '',
      '模型保持 @30875-fall26-openai/gpt-5-nano；80调用、1,000,000 token、请求300秒、整场1200秒、4并发和2次传输重试全部不变。', '',
      'decisions.json 保存可审阅的函数级取舍与应用条件；study.json、各fixed_inputs、请求响应、评分缓存、verification.json和manifest.json 保存复核依据。当前默认流程仍是原开发实现，没有暗中更改feature配置或提交代码。', '']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Wrote decisions.json and REPORT.md; default pipeline unchanged')
if __name__=='__main__':main()
