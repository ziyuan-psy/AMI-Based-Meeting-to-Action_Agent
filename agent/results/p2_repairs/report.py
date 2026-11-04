"""Regenerate the offline repair report from saved scoring; no model calls."""
from pathlib import Path
import json
import sys
from collections import Counter
import copy

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path[:0]=[str(REPO/'agent/src'),str(REPO/'agent/logs/trace_step_deps')]
from mta.observation import write_json,utc_now
from mta.evaluate import prf,TYPES

ROOT=Path(__file__).resolve().parent
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def aggregate(rows,arms):
    result={}
    for arm in arms:
        result[arm]={}
        for typ in TYPES:
            eligible=[r['arms'][arm]['metrics'][typ] for r in rows if 'unscored' not in r['arms'][arm]['metrics'][typ]]
            result[arm][typ]={**prf(*(sum(r[k] for r in eligible) for k in ('tp','sys','gold'))),'meetings':len(eligible)}
    return result

cases={}
for case in ['repair-v1','fresh-v1','repair-v3']:
    path=ROOT/case/'comparison.json'
    if path.exists():cases[case]=load(path)
cohorts={}
for case,data in cases.items():
    rows=[r for r in data['meetings'] if r['status']=='COMPLETED']
    if case!='repair-v1' and 'repair-v1' in cases:
        anchors={r['meeting_id']:r['arms']['teammate'] for r in cases['repair-v1']['meetings'] if r['status']=='COMPLETED'}
        rows=copy.deepcopy(rows)
        for row in rows:
            if row['meeting_id'] in anchors:
                row['arms']['teammate']=copy.deepcopy(anchors[row['meeting_id']])
    paired=[r for r in rows if 'before' in r['arms']]
    cohorts[case]={'completed_scoring':[r['meeting_id'] for r in rows],
        'before_after_paired_meetings':[r['meeting_id'] for r in paired],
        'before_after':aggregate(paired,['before','after']),
        'teammate_after':aggregate(rows,['teammate','after'])}

runtimes={}
for case in ['repair-v1','fresh-v1','repair-v3']:
    root=ROOT/case
    study=load(root/'study.json')
    source=Path(study['source_study'])
    entries=[]
    for mid in study['meetings']:
        p=root/'after'/(mid+'.result.json')
        if not p.exists():continue
        result=load(p);before=load(source/'current'/(mid+'.result.json'))
        row={'meeting_id':mid,'status':result['status'],'before_status':before['status'],
            'seconds':result.get('seconds'),
            'stage_seconds':result.get('stage_seconds',{}),
            'unknown_usage_tokens':result['runtime_control']['unknown_usage_tokens'],
            'transport_errors':[{'stage':a['stage'],'error_type':a['error_type']} for a in result['runtime_control']['attempts'] if a.get('error_type')],
            'before_attempts':before['runtime_control']['attempted_calls'],
            'before_charged_tokens':before['runtime_control']['charged_tokens'],
            'new_api_attempts':result['runtime_control']['attempted_calls'],
            'actual_new_usage':result['actual_new_usage'],
            'combined_attempts':result.get('whole_meeting_budget',{}).get('combined_attempts'),
            'combined_tokens':result.get('whole_meeting_budget',{}).get('combined_charged_tokens'),
            'normalized_identical':result.get('normalized_identical_to_before')}
        if result['status']=='COMPLETED':
            meta=load(Path(result['run_metadata']))
            p2=load(root/'after/s2'/(mid+'.p2.json'))
            old_p2=source/'current/s2'/(mid+'.p2.json')
            row['before_recordable']=sum(i['recordable'] for i in load(old_p2)['items']) if before['status']=='COMPLETED' and old_p2.exists() else None
            row['recordable']=sum(i['recordable'] for i in p2['items'])
            row['accepted']=sum(i['state']=='accepted' for i in p2['items'])
            row['pending']=sum(i['state']=='pending' for i in p2['items'])
            row['termination_reasons']=dict(Counter(i['termination_reason'] for i in p2['items']))
            row['missing_statement_selection']=sum(i['checks']['statement']['reason']=='current_meeting_support_required' for i in p2['items'])
            row['admission']=meta.get('p2_admission')
            row['quote_bindings']=sum(bool(e.get('evidence_bindings')) for i in p2['items'] for e in i['history'])
        entries.append(row)
    runtimes[case]=entries

primary_case='repair-v3' if 'repair-v3' in cases else 'repair-v1'
write_json(ROOT/'summary.json',{'created_at':utc_now(),'primary_fixed_input_case':primary_case,'scope':'SB development: frozen-input causal check plus independent fresh run, model-judged draft gold; not final evaluation.',
    'cohorts':cohorts,'runtime':runtimes,'reference_policy':'Identical reference output/gold/model uses the first controlled-study three-vote reference in all published cohorts; raw duplicate judgments are preserved. See reference_policy.json.'})
lines=['# P2 修复与回归结果','',
       '本次修复普通 P2 的证据提交、辅助字段连带隔离和无效调用。原始 80 次/100 万 token 预算、来源验证与 Agent 自主选择保留。','',
       '## 固定输入的前后对照','',
       '最终策略版 3 的 repair-v3 复用第八步的抽取/合并样本。三场 normalize 输入逐项相同；原上游调用、计入 token 和耗时先扣除，P2 没有取得缓存带来的免费额度。',
       '旧 TS3006b 整场失败，不作为空记录计分。严格前后指标只覆盖 IS1008a、TS3006a 两个共同完成的会议。',
       'TS3006b 使用修复后 TS3006a 的新历史记录，展示连续流程效果；两个严格配对会议均为各自系列的第一次会议。','',
       '| 类型 | 修复前 P/R/F1 | 修复后 P/R/F1 |','| --- | --- | --- |']
def cell(r):return f"{r['precision']:.3f} / {r['recall']:.3f} / **{r['f1']:.3f}** ({r['tp']}/{r['sys']}/{r['gold']})"
controlled=cohorts.get(primary_case)
if controlled:
    for typ in TYPES:
        lines.append('| '+typ+' | '+cell(controlled['before_after']['before'][typ])+' | '+cell(controlled['before_after']['after'][typ])+' |')
lines+=['','括号为 TP/系统条数/gold。一对一同类型语义对齐、三票多数；前次已评判的相同文本列表复用原投票。gold 为开发草稿，尚未独立人工复核。','',
        '| 会议 | 原整场调用/状态 | 原可记录条目 | 修复后新 API 尝试 | 计入上游后的调用 | 修复后可记录 | 接受/待确认 |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- |']
for r in runtimes[primary_case]:
    previous=r.get('before_recordable')
    lines.append(f"| {r['meeting_id']} | {r['before_attempts']} / {r['before_status']} | {previous if previous is not None else '—'} | {r['new_api_attempts']} | {r['combined_attempts']} | {r.get('recordable','—')} | {r.get('accepted',0)}/{r.get('pending',0)} |")
lines+=['','新 API 尝试列是真正新增的请求，合计调用还含原始上游预留。缓存运行耗时不作空缓存整场速度宣传。',
        '首轮 repair-v1 的 TS3006b 成功 native pipeline 完成后，收尾脚本曾因旧失败运行缺少 normalize 导出副本而报错；',
        '从旧 native checkpoint 核对相同输入并从成功 metadata 恢复汇总，未改写原模型/生产产物、未新增调用。',
        '见 [恢复记录](repair-v1/postprocessing_recovery.json)。随后已修复该路径回退，评分使用原捕获源码。','',
        '| 会议 | 原整场计入 token | 修复后计入上游的 token |',
        '| --- | ---: | ---: |']
for r in runtimes[primary_case]:
    lines.append(f"| {r['meeting_id']} | {r['before_charged_tokens']:,} | {r['combined_tokens']:,} |")
lines+=['','计入 token 包含未知用量失败请求的保守预留，不等同于供应商账单。','',
        '## 最终版与队友基线的三场对照','',
        '以下为最终策略 3 的固定输入运行，对照第八步冻结的队友策略产物；三场均有结果。队友参考列表统一复用最早完成的三票判断，原始重复判断保留，见 reference_policy.json。','',
        '| 类型 | 队友 P/R/F1 | 最终策略 3 P/R/F1 |','| --- | --- | --- |']
for typ in TYPES:
    lines.append('| '+typ+' | '+cell(controlled['teammate_after']['teammate'][typ])+' | '+cell(controlled['teammate_after']['after'][typ])+' |')
lines+=['','最终版本次三场的 F1 仍低于队友基线。修复相对故障版改善了保留条目和调用消耗，但尚不能据此认定整体质量已超过队友版本；待确认关系、假阳性和模型判断波动仍需复核。','',
        '## 空缓存整场复验','',
        '首轮策略版 2 的 fresh-v1 不复用抽取/合并缓存，实际调用生产 pipeline 的全部阶段，预算不扣虚拟调用。最后的格式/路由边界补丁版 3 使用 repair-v3 及 focused-v3 重新验证，不把 v2 分数冒充 v3 分数。',
        '它是独立新采样，候选可能变化，不能把全部分数变化都归因于 P2。','',
        '| 会议 | 状态 | 实际秒 | 实际整场尝试 | 已知 input+output token | 未知用量保守计入 | 可记录 | 接受/待确认 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |']
for r in runtimes['fresh-v1']:
    usage=r['actual_new_usage'];lines.append(f"| {r['meeting_id']} | {r['status']} | {r['seconds']} | {r['new_api_attempts']} | {usage['in']+usage['out']:,} | {r['unknown_usage_tokens']:,} | {r.get('recordable','—')} | {r.get('accepted',0)}/{r.get('pending',0)} |")
lines+=['','未知用量是失败请求按预留额度保守计入，不是供应商报告的真实消耗；调用次数包含这些重试。','',
        '| 会议 | 抽取秒 | 合并秒 | P2＋摘要秒 | 已记录 transport 错误 |',
        '| --- | ---: | ---: | ---: | --- |']
for r in runtimes['fresh-v1']:
    stages=r['stage_seconds'];errors=', '.join(e['stage']+': '+e['error_type'] for e in r['transport_errors']) or '无'
    lines.append(f"| {r['meeting_id']} | {stages.get('extract')} | {stages.get('consolidate')} | {stages.get('agent+abstract')} | {errors} |")
lines+=['','本轮调用数量下降，整场耗时没有同步改善。较长耗时主要位于未修改的抽取/合并阶段，并记录了服务端错误重试；单次运行不能估计延迟稳定性。','']
fresh=cohorts.get('fresh-v1')
if fresh:
    lines+=['','三场空缓存结果与固定的队友策略真实产物对照（同一开发 gold，不能与上方两场表直接混用分母）：',
            '队友产物和 gold 完全相同，展示表统一采用最早完成的固定输入组参考投票；空缓存组重复评判参考列表的原投票仍保留，未据分数择优。见 reference_policy.json。','',
            '| 类型 | 队友 P/R/F1 | 修复后空缓存 P/R/F1 |','| --- | --- | --- |']
    for typ in TYPES:lines.append('| '+typ+' | '+cell(fresh['teammate_after']['teammate'][typ])+' | '+cell(fresh['teammate_after']['after'][typ])+' |')
focused_path=ROOT/'focused-v3/run.json'
if focused_path.exists():
    focused=load(focused_path)
    lines+=['','## 最终边界补丁的针对性验证','',
        '策略版 3 补充：无冲突 pass 的遗漏空 affected_fields、无决策作用的 confidence、从明确对话 ID 推导遗漏 meeting_id，以及本地字段未解决时允许当前会议补查。',
        '以下三个原失败输入共享一份原始 80 次/100 万 token controller，在线运行不读取 gold；不是另外三次整场评估。','',
        '| 原输入 | 状态 | 可记录 | 实际调用 | 终止原因 |','| --- | --- | --- | ---: | --- |']
    for r in focused['cases']:
        lines.append(f"| {r['item_key']} | {r['state']} | {r['recordable']} | {r['calls']} | {r['termination_reason']} |")
    lines+=['','[原响应有限回放](focused-v3/replay.json) 新增 API 为 0；只移除格式障碍，不把语义判断改成 pass：',
        '旧 IS1008a 响应中的未读 A.10 仍被拒绝；旧 hidden-features 响应中的冲突仍保留不确定。当前在线复验另保存实际模型选择。','']
lines+=['','## 机制与限制','',
        '- 平铺 assess_item：每个肯定检查必须选择实际交付的对话 ID；代码绑定原文，伪造/未读/未来证据和文档命名空间错误仍拒绝。',
        '- 未确认辅助字段先清除再做字段校验；支持的核心陈述保留，待确认字段产生问题。任务文本本身不受支持时仍隔离。',
        '- next_action 必填；accept 提议若仍有辅助字段缺口，实际状态为 pending，不默认开启无意义检索。',
        '- 统一 controller 的 admission view 为未开始条目保留首次尝试、为摘要预留三次尝试；重试同样计数。软额度不足保留已有结果，真实 API 失败仍是整场失败。',
        '- 固定输入结果支持“减少协议失败及 P2 新增漏项”的结论，不证明原始候选/类型已全部正确。更多保留条目仍可能是假阳性。',
        '- source/range 校验证明出处，不自动证明语义蕴含。support judge 的原判定与未匹配条目保存在各 scoring JSON 中，需独立复核。',
        '- 本轮每组仅一次；未访问留出集，没有最终评价或独立人工确认，也未调整 gold 来追分。','',
        '## 复查与复现','',
        '- [最终固定输入比较](repair-v3/comparison.json)、[首轮固定输入比较](repair-v1/comparison.json)、[策略 2 空缓存比较](fresh-v1/comparison.json)、[汇总数据](summary.json)。',
        '- 每组 study.json 记录源码/模型/配置与输入哈希，source/ 保存实际源码。',
        '- after/s2/ 保存 JSON、报告、检查与澄清 sidecar、native stage trace 与 checkpoint 索引；after/*.requests.jsonl 保存提交内容。',
        '- 上游扣除事件 captured_upstream_budget_reserved、调用分配 p2_admission 和原始响应使调用消耗可追溯。',
        '- [协议与运行说明](../../docs/P2_PROTOCOL_REPAIR.md)。命令均拒绝覆盖已有运行。','']
(ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({'cohorts':cohorts,'completed_runs':{k:len(v) for k,v in runtimes.items()}},ensure_ascii=False,indent=2))
