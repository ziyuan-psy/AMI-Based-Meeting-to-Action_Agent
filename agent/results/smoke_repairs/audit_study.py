"""Audit the new online run against the immutable 3b886f6 smoke artifacts; no gold/API calls."""
import argparse
import json
import hashlib
from pathlib import Path
import sys

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
BEFORE=REPO/'agent/results/frozen_smoke/fresh-v1'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def events(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def audit(root):
    sys.path.insert(0,str(root/'source/agent/src'))
    from mta import config
    from mta.p2 import validate_outputs
    from mta.validate import check
    from mta.store import Store
    from mta.transcript_parser import parse_transcript_file
    from mta.statement_revision import deletion
    study=load(root/'study.json')
    for name,value in study['runtime']['config'].items():setattr(config,name,value)
    config.DATA=root/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts';config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    for rel,h in study['artifact_sha256'].items():assert sha(root/rel)==h,rel
    for rel,h in load(BEFORE.parent/'manifest.json')['artifact_sha256'].items():assert sha(BEFORE.parent/rel)==h,rel
    for rel,h in load(root/'protected_before.json').items():assert sha(REPO/rel)==h,rel
    rows=[];cases=[];all_links=[];errors=[]
    outputs={};sidecars={}
    for mid in study['meetings']:
        out=root/'run/s2';path=root/'run'/(mid+'.result.json')
        if not path.exists():rows.append({'meeting_id':mid,'status':'NOT_RUN'});errors.append(mid+': missing result');continue
        r=load(path);meta=load(out/(mid+'.run.json'));stage=events(out/(mid+'.stages.jsonl'))
        c=r['runtime_control'];attempts=c['attempts'];counts={s:sum(a['stage']==s for a in attempts) for s in sorted({a['stage'] for a in attempts})}
        row={'meeting_id':mid,'status':r['status'],'seconds':r.get('seconds',meta.get('elapsed_seconds')),
            'calls':c['attempted_calls'],'known_tokens':sum(a.get('input_tokens',0)+a.get('output_tokens',0) for a in attempts if a.get('usage_known')),
            'charged_tokens':c['charged_tokens'],'unknown_usage_tokens':c['unknown_usage_tokens'],'stages':counts}
        rows.append(row)
        assert c['limits']==load(BEFORE/'run'/(mid+'.result.json'))['runtime_control']['limits']
        assert c['attempted_calls']<=80 and c['charged_tokens']<=1000000 and c['max_inflight_seen']<=4
        assert meta['model']==study['runtime']['model'] and meta['config']==study['runtime']['config']
        assert meta['input']['sha256']==sha(config.TRANSCRIPTS/(mid+'.txt'))
        assert load(Path(meta['checkpoints']['parsed']))==parse_transcript_file(config.TRANSCRIPTS/(mid+'.txt'))
        assert meta['code']['source_sha256']==study['runtime']['code']['source_sha256']
        assert [e['seq'] for e in stage]==list(range(len(stage)))
        assert Path(meta['cache_directory']).resolve()==(root/'run/_cache').resolve()
        hits=[e for e in stage if e.get('source')=='cache' and e['stage'] in ('extract','consolidate','decision_consolidate','select')]
        hits += [e for e in stage if e.get('cached_samples',0)>0]
        assert not hits and not any(e['event']=='captured_upstream_budget_reserved' for e in stage)
        row['historical_response_cache_hits']=len(hits)
        if r['status']!='COMPLETED':row['failure']=r.get('failure');errors.append(mid+': '+r['status']);continue
        assert stage[-1]['event']=='finished' and stage[-1]['status']=='COMPLETED'
        assert all(counts.get(s,0)>0 for s in ['extract','consolidate','decision_consolidate','select','p2_check','abstract'])
        records=load(out/(mid+'.json'));p2=load(out/(mid+'.p2.json'));questions=load(out/(mid+'.clarifications.json'))
        omitted_path=meta['checkpoints'].get('verified_omitted')
        omitted=load(Path(omitted_path))['outcomes'] if omitted_path else []
        all_outcomes=p2['items']+omitted
        assert all(o['recordable'] and o['record_id'] is None for o in omitted)
        assert all(sum(i['type']==t for i in records)<=config.MAX_ITEMS[t] for t in config.MAX_ITEMS)
        outputs[mid]=records;sidecars[mid]=p2
        store=Store(mid,allow_prior=True,run_dir=out)
        validation=check(records,store)+validate_outputs(p2,questions,records,store)
        errors.extend(mid+': '+e for e in validation);row['validation_errors']=validation
        for prior in meta.get('prior_context',[]):
            assert prior['meeting_id'] in store.visible_ids('prior')
            assert sha(out/(prior['meeting_id']+'.json'))==prior['record_sha256']
            assert sha(config.TRANSCRIPTS/(prior['meeting_id']+'.txt'))==prior['transcript_sha256']
        report=out/(mid+'_report.md');text=report.read_text(encoding='utf-8')
        assert all(q['question'] in text for q in questions['requests'])
        for key,path in [('output_sha256',out/(mid+'.json')),('report_sha256',report),('p2_sha256',out/(mid+'.p2.json')),('clarifications_sha256',out/(mid+'.clarifications.json'))]:assert sha(path)==r[key]
        row.update(record_counts={t:sum(i['type']==t for i in records) for t in ['actions','decisions','problems']},
            p2_items=len(all_outcomes),recordable=sum(i['recordable'] for i in p2['items']),accepted=sum(i['state']=='accepted' for i in p2['items']),
            verified_omitted=len(omitted),clarification_requests=len(questions['requests']),core_rechecks=sum(e['action']=='core_recheck_offered' for i in all_outcomes for e in i['history']))
        published_keys={i['item_key'] for i in p2['items']}
        for i in all_outcomes:
            assert i['initial_context']['earlier_meetings']==store.visible_ids('prior')
            for w in i['relation_evidence']:
                if w['state']=='supported' and i['item_key'] in published_keys:all_links.append({'meeting_id':mid,'item_key':i['item_key'],**w})
            for e in i['history']:
                for q in e.get('tool_result',{}).get('results',[]):
                    if 'da_id' in q:assert q['da_id'].split('.')[0] in store.visible_ids()
                if e.get('action')=='revise_statement':cases.append({'meeting_id':mid,'item_key':i['item_key'],'item_text':i['item_text'],'event':e})
    empty=[c for c in cases if any(not c['event'].get('raw_tool_input',{}).get(k) for k in ('remove_suffix','reason','evidence_da_ids'))]
    menus=[]
    for e in events(root/'requests.jsonl'):
        for tool in e.get('tools') or []:
            if tool['name']!='revise_statement':continue
            props=tool['input_schema']['properties'];suffixes=props['remove_suffix'].get('enum',[]);ids=props['evidence_da_ids']['items'].get('enum',[])
            assert suffixes and all(s.strip() for s in suffixes) and ids
            menus.append({'stage':e['stage'],'suffixes':suffixes,'delivered_current_ids':ids})
    old1=load(BEFORE/'run/s2/IS1008a.json');old2=load(BEFORE/'run/s2/TS3006a.json')
    slides=lambda records:[r for r in records if r['type']=='actions' and 'slides' in r['text'].lower()]
    design=lambda records:[r for r in records if r['type']=='actions' and 'working design' in r['text'].lower()]
    functional={'slides':{'before':slides(old1),'after':slides(outputs.get('IS1008a',[]))},
        'id_working_design':{'before':design(old2),'after':design(outputs.get('TS3006a',[]))},
        'empty_revision_calls':{'before':1,'after':len(empty)},'cross_meeting_links':{'before':0,'after':len(all_links)}}
    save(root/'links.json',all_links);save(root/'revision_cases.json',{'natural_cases':cases,'empty_calls':empty,'advertised_menus':menus})
    probe={'status':'NOT_RUN'}
    if (root/'revision_probe/outcome.json').exists():
        item=load(root/'revision_probe/outcome.json');pr=load(root/'revision_probe/run.json');seen={r['da_id']:r for r in item['initial_context']['observed_dialogue']}
        revised=[e for e in item['history'] if e.get('outcome')=='revised_requires_recheck']
        for e in item['history']:
            for q in e.get('tool_result',{}).get('results',[]):
                if 'da_id' in q:seen[q['da_id']]=q
            if e.get('outcome')=='revised_requires_recheck':
                a=e['raw_tool_input'];assert deletion(e['before_text'],a['remove_suffix'],a['evidence_da_ids'],seen,'IS1008a')==e['after_text']
        rechecks=[e for e in item['history'] if revised and e.get('outcome')=='checked' and e['step']>revised[0]['step'] and e.get('assessed_text')==item['item_text']]
        deadline=item['fields'].get('deadline')
        deadline_ok=deadline is None or ('friday' not in deadline.lower() and item['checks']['deadline']['result']=='pass' and item['checks']['deadline']['quotes'])
        ok=bool(len(revised)==1 and rechecks and item['recordable'] and 'Friday' not in item['item_text'] and deadline_ok)
        probe={'status':'PASSED' if ok else 'NOT_DEMONSTRATED','state':item['state'],'recordable':item['recordable'],'text':item['item_text'],
            'valid_revisions':len(revised),'fresh_rechecks':len(rechecks),'deadline':deadline,'actual_usage':pr['actual_usage'],'is_natural_case':False}
    checks={'all_pipeline_and_artifact_checks':not errors and len(outputs)==3,
        'slides_present':bool(functional['slides']['after']),'id_working_design_present':bool(functional['id_working_design']['after']),
        'no_empty_revision_calls':not empty,'published_cross_meeting_link':bool(all_links),'same_controlled_revision_passed':probe['status']=='PASSED'}
    before_summary=load(BEFORE/'summary.json')
    before_totals={'calls':sum(r['api_attempts'] for r in before_summary['meetings']),
        'tokens':sum(r['known_tokens'] for r in before_summary['meetings']),'seconds':round(sum(r['seconds'] for r in before_summary['meetings']),1)}
    summary={'kind':study['kind'],'base_commit':study['base_commit'],'configuration_changes':study['config_changes'],
        'checks':checks,'result':'PASSED' if all(checks.values()) else 'GAPS_REMAIN','errors':errors,'meetings':rows,
        'totals':{'calls':sum(r.get('calls',0) for r in rows),'tokens':sum(r.get('known_tokens',0) for r in rows),'seconds':round(sum(r.get('seconds') or 0 for r in rows),1)},
        'before_totals':before_totals,'comparison_scope':'Single fresh run per version; functional development regression, no F1 or stability claim.',
        'functional_comparison':functional,'controlled_probe':probe,'gold_scored':False,'previous_smoke_unchanged':True,'protected_files_unchanged':len(load(root/'protected_before.json'))}
    save(root/'summary.json',summary)
    lines=['# 试跑问题修复：同输入开发回归','',f"总体结果：**{summary['result']}**。基于 `3b886f6`；本次修改尚未 commit。",'',
        '与上轮相同的 IS1008a、TS3006a、TS3006b TXT、参与者元数据、scenario 文档、Portkey gpt-5-nano 和预算。新建独立空缓存，所有阶段真实运行；历史仅来自本轮更早记录。只有 P2/selection 策略版本号改变。',
        '', '| 检查 | 结果 |','| --- | --- |']
    lines += ['| '+k+' | '+str(v)+' |' for k,v in checks.items()]
    lines += ['', '| 会议 | 状态 | 秒 | 调用 | 已知 token | 可记录/输入 | 澄清问题 |', '| --- | --- | ---: | ---: | ---: | --- | ---: |']
    lines += [f"| {r['meeting_id']} | {r['status']} | {r.get('seconds','—')} | {r.get('calls','—')} | {r.get('known_tokens','—')} | {r.get('recordable','—')}/{r.get('p2_items','—')} | {r.get('clarification_requests','—')} |" for r in rows]
    lines += ['', '## 调用与时间对比','', '| 主试跑 | 调用 | API 已知 token | 秒 |','| --- | ---: | ---: | ---: |',
        f"| 修复前 | {before_totals['calls']} | {before_totals['tokens']} | {before_totals['seconds']} |",
        f"| 本次 | {summary['totals']['calls']} | {summary['totals']['tokens']} | {summary['totals']['seconds']} |",'',
        '本轮耗时更长，不能宣称速度改善。上表不混入中间失败实验或独立修正诊断；单次真实模型运行有输出和服务时延变化，没有进行显著性或稳定性评估。']
    lines += ['', '## 前后差异','',f"- slides Action：{len(functional['slides']['before'])} → {len(functional['slides']['after'])}。",
        f"- ID working design Action：{len(functional['id_working_design']['before'])} → {len(functional['id_working_design']['after'])}。",
        f"- 自然运行有限修正空参数调用：1 → {len(empty)}。",
        f"- 有来源证据的最终跨会议关系：0 → {len(all_links)}。",
        '', '完整条目和引用见 summary.json；每一条关联的旧记录、双方原文和模型理由见 links.json。']
    if all_links:
        lines += ['', '## 实际发布的跨会议链路','', '以下为模型输出及 Codex 原文核查材料，未新增人工确认，也未把旧的 TV 范围更新标签移用到新案例。']
        for w in all_links:
            item=next(i for i in sidecars[w['meeting_id']]['items'] if i['item_key']==w['item_key'])
            lookups=[{'tool':e['action'],'args':e.get('args')} for e in item['history'] if e.get('outcome')=='lookup' and e['action'] in ('get_prior_items','read_prior_item','search_dialogue','read_dialogue')]
            lines += ['',f"旧记录 `{w['prior_item_id']}`：{w['before']['text']}",
                '', '实际检索/读取：`'+json.dumps(lookups,ensure_ascii=False)+'`。',
                '', 'Agent 选择：`'+w['relation']+'`；理由：'+w['reason'],
                '',f"新记录 `{item['record_id']}`：{w['after']['text']}",
                '', '旧证据：']+['- `'+q['da_id']+'` '+q['quote'] for q in w['prior_quotes']]
            lines += ['', '当前证据：']+['- `'+q['da_id']+'` '+q['quote'] for q in w['current_quotes']]
            lines += ['',f"当前记录状态为 `{item['state']}`；未确认字段：`{','.join(item['unresolved_fields'])}`。旧 JSON 保持不变。"]
    lines += [
        '', '## 证据与局限','',
        '- captured_cap_comparison.json 使用完全相同的旧候选池重放，可单独归因排序修复。完整试跑重新调用抽取、合并和投票，输出也包含模型变化，不能把全部内容差异都归因于本次代码。',
        '- 有限修正受控诊断沿用上轮相同的真实证据和 Friday 故障输入，独立保存在 revision_probe/；不计作自然成功案例或 F1。',
        '- 请求、可选工具及模型上下文见 requests.jsonl；阶段结果、原始 checkpoint、P2 检查、澄清和报告在 run/。',
        '- 所有源码、模型、配置和输入哈希见 study.json 与 source/。上轮试跑、UI 和标注文档哈希保持原样。',
        '- 本轮是开发回归，没有读取 gold、计算 F1、运行最终 evaluation，也未 commit。']
    if errors:lines += ['', '## 错误','']+['- '+e for e in errors]
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'result':summary['result'],'checks':checks,'totals':summary['totals'],'controlled_probe':probe},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);a=p.parse_args();audit(a.root.resolve())
