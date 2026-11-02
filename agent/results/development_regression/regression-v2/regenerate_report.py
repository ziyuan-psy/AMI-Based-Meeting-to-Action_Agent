"""Offline inspection/report of a completed small development regression. No model calls."""
from pathlib import Path
import hashlib
import json
import sys
from collections import Counter

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/regression.py').is_file())
sys.path[:0]=[str(REPO/'agent/src'),str(REPO/'agent/logs/trace_step_deps')]
from mta import regression as reg
from mta import config
from mta.observation import utc_now,write_json,sha256
from mta.evaluate import deadline_match

root=Path(sys.argv[1]).resolve()
data=reg.load(root/'comparison.json')
study=reg.load(root/'study.json')
all_maps={arm:{s:g for r in data['per_meeting'] if r['status']=='COMPLETED' for s,g in r['arms'][arm]['matches'].items()}
          for arm in ['baseline','current']}
audits=[]
for mid in study['meetings']:
    row={'meeting_id':mid,'runtime':{}}
    for arm in ['baseline','current']:
        path=root/arm/(mid+'.result.json')
        result=reg.load(path) if path.exists() else {'status':'NOT_RUN'}
        usage=result.get('usage',{})
        row['runtime'][arm]={'status':result['status'],'reason':result.get('reason'),
            'seconds':result.get('elapsed_seconds'),'model_attempts':result.get('runtime_control',{}).get('attempted_calls'),
            'known_tokens':usage.get('in',0)+usage.get('out',0),
            'unknown_usage_charge':result.get('runtime_control',{}).get('unknown_usage_tokens')}
    scored=next((r for r in data['per_meeting'] if r['meeting_id']==mid and r['status']=='COMPLETED'),None)
    if scored:
        golden={r['id']:r for r in reg.load(root/'gold'/(mid+'.json'))}
        row['field_checks']={}
        for arm in ['baseline','current']:
            outputs={r['id']:r for r in reg.load(root/arm/'s2'/(mid+'.json')) if r['type']!='abstract'}
            matches=scored['arms'][arm]['matches']
            fields={name: {'correct':0,'total':0} for name in ['owner_exact','owner_basis','deadline','status','prior_context_needed']}
            edge_counts=[0,0,0]
            for sid,gid in matches.items():
                s,g=outputs[sid],golden[gid]
                if g.get('status') not in (None,'unknown','unreviewed'):
                    fields['status']['total']+=1; fields['status']['correct']+=s['status']==g['status']
                if isinstance(g.get('prior_context_needed'),bool):
                    fields['prior_context_needed']['total']+=1
                    fields['prior_context_needed']['correct']+=s.get('prior_context_needed')==g['prior_context_needed']
                    predicted={(all_maps[arm].get(e['id'],'UNMATCHED:'+e['id']),e['relation']) for e in s.get('related_items',[])}
                    expected={(e['id'],e['relation']) for e in g.get('related_items',[])}
                    edge_counts[0]+=len(predicted&expected); edge_counts[1]+=len(predicted); edge_counts[2]+=len(expected)
                if g['type']=='actions':
                    if g.get('owner_basis')!='unreviewed':
                        fields['owner_exact']['total']+=1; fields['owner_exact']['correct']+=set(s.get('owner') or [])==set(g.get('owner') or [])
                        fields['owner_basis']['total']+=1; fields['owner_basis']['correct']+=s.get('owner_basis')==g.get('owner_basis')
                    if g.get('deadline_basis')!='unreviewed':
                        fields['deadline']['total']+=1; fields['deadline']['correct']+=deadline_match(s,g)
            for metric in fields.values(): metric['rate']=round(metric['correct']/metric['total'],3) if metric['total'] else None
            fields['relations_on_matched_items']={**reg.prf(*edge_counts),'scope':'Conditional on matched current items with reviewed draft prior flags, each arm has its own denominator.'}
            row['field_checks'][arm]=fields
        p2=reg.load(root/'current/s2'/(mid+'.p2.json'))
        row['protocol_diagnostics']={'items_with_pass_but_no_quote_selection':[],
                                     'quarantined_statement_missing_current_selection':[]}
        for outcome in p2['items']:
            submitted=[e for e in outcome['history'] if e.get('outcome')=='checked']
            missing=[{'step':e['step'],'checks':[name for name,f in e.get('submitted_checks',{}).items()
                         if f.get('result')=='pass' and not f.get('quotes')]}
                     for e in submitted]
            missing=[e for e in missing if e['checks']]
            if missing:
                row['protocol_diagnostics']['items_with_pass_but_no_quote_selection'].append({'item_key':outcome['item_key'],'submissions':missing})
            if (not outcome['recordable'] and outcome['checks']['statement']['reason']=='current_meeting_support_required'
                    and submitted and submitted[-1].get('submitted_checks',{}).get('statement',{}).get('result')=='pass'
                    and not submitted[-1]['submitted_checks']['statement'].get('quotes')):
                row['protocol_diagnostics']['quarantined_statement_missing_current_selection'].append(outcome['item_key'])
        pre_matches=scored['arms']['current_before_p2']['matches']
        row['quarantined_previously_matched_items']=[]
        for outcome in p2['items']:
            if outcome['recordable']: continue
            i=int(outcome['item_key'].rsplit('#',1)[1])
            identifier=f'{mid}.pre.{i}'
            if identifier in pre_matches:
                row['quarantined_previously_matched_items'].append({'item_key':outcome['item_key'],'gold_id':pre_matches[identifier],
                    'text':outcome['item_text'],'termination_reason':outcome['termination_reason'],
                    'unresolved':{k:v['reason'] for k,v in outcome['checks'].items() if v['result'] not in ('pass','not_applicable')}})
        # Coverage only: provenance validators already check exact quotes, source hashes and tool delivery.
        requests=reg.load(root/'current/s2'/(mid+'.clarifications.json'))['requests']
        row['trace']={'p2_items':len(p2['items']),'items_with_history':sum(bool(i['history']) for i in p2['items']),
                      'clarifications':len(requests),'lookup_steps':sum(e.get('outcome')=='lookup' for i in p2['items'] for e in i['history']),
                      'lookups_with_actual_results':sum('tool_result' in e for i in p2['items'] for e in i['history'] if e.get('outcome')=='lookup')}
        row['support']={}
        for arm,judged in scored['support'].items():
            counts=Counter((r.get('verdict') or {}).get('statement','not_judged') for r in judged)
            row['support'][arm]={'counts':dict(counts),'judged':sum(v for k,v in counts.items() if k!='not_judged'),
                                 'supported_rate':round(counts['supported']/len(judged),3) if judged and 'not_judged' not in counts else None}
    audits.append(row)
write_json(root/'audit.json',{'created_at':utc_now(),'scope':'Offline audit; field labels are draft annotations; support judges see at most 10 dialogue references, no document entailment judgment.', 'meetings':audits})

declines=[kind for kind in reg.TYPES if data['aggregate']['current'][kind]['f1']<data['aggregate']['baseline'][kind]['f1']]
failed=[row['meeting_id']+' ('+str(row['runtime']['current']['reason'])+')' for row in audits if row['runtime']['current']['status']!='COMPLETED']
lines=['# 第八步：开发回归检查','',
       '**结论：本轮发现质量或整场完成率退步，需要修复后再考虑冻结交付。**' if declines or failed else '**结论：本轮小样本检查完成，泛化与稳定性仍需独立评估。**',
       f"已完成配对评分 {len(data['eligible_meetings'])} 场；当前版未完成：{', '.join(failed) or '无'}。F1 下降类型：{', '.join(declines) or '无'}。",
       'P2 证据选择协议未完成是明确问题：模型多次标记 pass 却没有选择当前对话 ID。现有验证保持严格，未通过自动补证据或放宽预算来追分。',
       '代码回归为 240 项测试和 11 个子测试通过；本轮修复了 Windows checkpoint 临时路径长度问题。测试通过不代表语义质量通过。','',
       '本轮对照使用队友分支实际代码与第七步后的生产版本，保存全部尝试；不使用仓库旧 MVP 输出代替新分支结果。',
       '这是三场 SB 开发会议的一次对照，不是最终 evaluation、独立人工复核或稳定性检验。','',
       '## 固定口径','',
       f"- 队友代码：`{study['baseline_commit']}`；当前生产代码基于 `{study['current']['code']['git_commit']}`，本轮增加回归工具，并修复 checkpoint 原子写入临时路径长度问题。",
       '- 会议：'+', '.join(study['meetings'])+'；s2 顺序运行，同一版本仅使用自己的早期会议输出。',
       '- 两版解析结果完全相同；转写、participants、scenario 和当前开发 gold 均已复制并计算哈希。',
       '- 两边缓存从空目录开始。历史策略、提示词和工具来自冻结源文件；历史分支统一接入当前 LLM transport，以执行相同调用、token、超时、重试及并发限制。该接入不修改其抽取、合并或 Agent 决策逻辑。',
       '- 同一 Portkey gpt-5-nano 模型。评分沿用现有同类型、一对一、三票多数语义匹配；相同文本列表复用本轮投票。NA 占位类别不计分，提示词示例及对应输出共同排除。',
       '- gold 是现有开发草稿，部分内容待人工复核；只有 evaluator 读取标签。ES2004/TS3007 留出数据未用于本轮。',
       '- 每场仍为 80 次模型尝试、100 万 token、300 秒请求超时、1,200 秒整场超时和 4 路并发。评分有独立的同上预算，不与生成用量混在一起。',
       '- 未完成的会议不按空输出计分；两版均完成后才进入配对指标。P2 正常完成但隔离的条目则算作实际输出减少。','',
       '## 条目质量','',
       '括号内为 TP / 系统条数 / gold；汇总为 micro P/R/F1。current_before_p2 是当前 normalize 后的候选，不是可交付输出。','',
       '| 类型 | 队友 P / R / F1 | 当前 P / R / F1 | 当前 P2 前 P / R / F1 |',
       '| --- | --- | --- | --- |']
def cell(r): return f"{r['precision']:.3f} / {r['recall']:.3f} / {r['f1']:.3f} ({r['tp']}/{r['sys']}/{r['gold']}; {r['meetings']} 场)"
for kind in reg.TYPES:
    lines.append('| '+kind+' | '+' | '.join(cell(data['aggregate'][arm][kind]) for arm in ['baseline','current','current_before_p2'])+' |')
lines+=['','已计分会议：'+', '.join(data['eligible_meetings'])+'；未计分会议：'+(', '.join(data['unscored_meetings']) or '无')+'。','',
        '## P2 与待确认','', '| 会议 | 接受 | 待确认 | 隔离 | 仍可记录的待确认 | 澄清请求 | 文档观察/保留 | 已验证历史关系 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
for r in data['per_meeting']:
    if r['status']!='COMPLETED': continue
    p=r['p2']; lines.append(f"| {r['meeting_id']} | {p['accepted']} | {p['pending']} | {p['quarantined']} | {p['recordable_pending']} | {p['clarification_requests']} | {p['documents_observed']}/{p['documents_retained']} | {p['relations_supported']} |")
lines+=['','以下是评分在 P2 前匹配到 gold、随后被隔离的输入，须核对是正确拒绝了有问题的候选，还是过度拦截。自动匹配并非事实正确性的证明。','']
for row in audits:
    for item in row.get('quarantined_previously_matched_items',[]):
        lines.append(f"- `{item['item_key']}` → `{item['gold_id']}`：{item['text']} 停止：`{item['termination_reason']}`；未解决检查：`{json.dumps(item['unresolved'],ensure_ascii=False)}`。")
lines+=['','### 协议遵循诊断','',
        '有些检查由模型标为 pass，却没有选择 quotes 中的对话 ID。代码因此保留 current_meeting_support_required。',
        '这类失败表明证据选择协议未完成，不能直接解释成原始会议没有支持证据。修复不能通过自动认定支持或跳过验证来完成。',
        '原始 pass 未选引用的观察数还包含允许从 statement 派生的 status/conflict 检查，并不全部是错误；最终 statement 被隔离的第二个数量才是明确的阻塞症状。','']
for row in audits:
    if 'protocol_diagnostics' not in row:
        lines.append(f"- {row['meeting_id']}：整场未完成，未纳入完整条目的协议统计；部分检查保存在 checkpoint 中。")
        continue
    diagnostics=row.get('protocol_diagnostics',{})
    missing=diagnostics.get('quarantined_statement_missing_current_selection',[])
    lines.append(f"- {row['meeting_id']}：{len(diagnostics.get('items_with_pass_but_no_quote_selection',[]))} 条出现过 pass 未选引用；{len(missing)} 条最终因 statement 缺少当前引用而隔离：`{', '.join(missing)}`。")
lines+=['','## 运行与证据支持','', '| 会议 | 版本 | 状态 | 秒 | 模型尝试 | 已知 token | 未知用量保守计入 | 对话证据 supported/partial/unsupported |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | --- |']
for row in audits:
    for arm,r in row['runtime'].items():
        s=row.get('support',{}).get(arm,{})
        counts=s.get('counts',{})
        verdict_cell='/'.join(str(counts.get(k,0)) for k in ['supported','partial','unsupported']) if s else '未计分'
        lines.append(f"| {row['meeting_id']} | {arm} | {r['status']} | {r['seconds']} | {r['model_attempts']} | {r['known_tokens']:,} | {r['unknown_usage_charge'] or 0:,} | {verdict_cell} |")
lines+=['','以上为生成用量，评分用量在各 `scoring/<meeting>.json` 中单列；unknown usage charge 不是供应商报告的真实 token。',
        'Owner、deadline、status 的逐会议匹配项统计保存在 [audit.json](audit.json)，未复核占位字段不计入准确率。',
        '摘要随完整流程生成并保存，本轮未额外对摘要做语义评分。PDF 引用验证证明来源与选用关系，不能替代语义正确性判断。','',
        '## Trace 与复查入口','',
        '- [完整对照、匹配、未匹配条目与隔离原因](comparison.json)。',
        '- [逐会议字段、证据支持及 trace 覆盖审计](audit.json)。',
        '- `current/s2/<meeting>.run.json`：代码、模型、配置、实际预算与所有 checkpoint 路径。',
        '- `current/s2/<meeting>.stages.jsonl`：抽取候选、合并采样、投票、截断、normalize、调用与终止事件。',
        '- `current/s2/<meeting>.p2.json` / `.clarifications.json`：检查结论、工具返回、选用证据、停止原因、记录对应关系及待确认问题。',
        '- `<arm>/<meeting>.requests.jsonl`：模型实际请求；当前响应另见 stages，历史版本的响应与控制事件在同一 requests 文件中。',
        '- `baseline/s2/`、`current/s2/`：最终 JSON 与 Markdown；`source_baseline/`、`source_current/`：本轮源文件快照。',
        '- [study.json](study.json)：选择口径、输入与源码哈希；[parser_parity.json](parser_parity.json)：解析一致性。','',
        '此前人工核对的跨会议案例和第七步文档案例仍单独冻结，本轮全流程是否自主用到这些工具应以实际 trace 为准；没有调用不能写成已通过场景验证。','',
        '## 后续优先级','',
        '1. 修复普通 P2 检查的证据选择/提交协议，确保模型可以提交有效当前对话 ID；保留真实来源校验，不能自动把检索命中视为支持。',
        '2. 减少重复无进展和无效格式回合，使整场在原有 80 次/100 万 token 内完成，并为后续阶段保留调用余量。',
        '3. 先用保存的 normalize 输入定位 P2 前后变化，再按同一口径复跑整场；同时检查初始抽取和合并的差异，不能把独立采样的全部变化归因于 P2。',
        '修复和复验之前不建议冻结交付。上述 gold 匹配与证据判断仍需评估组员独立检查。','',
        '## 复现','',
        '从仓库根目录设置 `PYTHONPATH=agent/src`，配置项目现有依赖和 API 环境。目的目录必须是新的：','',
        '```powershell',
        'python agent/scripts/development_regression.py prepare agent/results/development_regression/new-run',
        'python agent/scripts/development_regression.py baseline agent/results/development_regression/new-run',
        'python agent/scripts/development_regression.py current agent/results/development_regression/new-run',
        'python agent/scripts/development_regression.py score agent/results/development_regression/new-run',
        '```','',
        '各阶段拒绝覆盖已有尝试。再次在线运行可能产生不同结果；固定版本不意味着模型输出逐字相同。',
        '本轮不调提示词、阈值或预算来追分。发现的质量退步应作为后续修复与交付冻结的依据。','']
(root/'REGRESSION.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({'eligible_meetings':data['eligible_meetings'],'aggregate':data['aggregate'],
                  'pending':sum(r.get('p2',{}).get('pending',0) for r in data['per_meeting'])},ensure_ascii=False,indent=2))
