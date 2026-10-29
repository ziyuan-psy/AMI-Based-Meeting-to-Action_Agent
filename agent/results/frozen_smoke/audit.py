"""Inspect native frozen-CLI outputs and revision witnesses; no model calls, no gold reads."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
BUNDLE=REPO/'agent/results/quality_repairs/release-v9'
sys.path.insert(0,str(BUNDLE/'source/agent/src'))
from mta import config
from mta.store import Store
from mta.p2 import validate_outputs
from mta.validate import check
from mta.statement_revision import deletion
from mta.transcript_parser import parse_transcript_file
from mta.observation import utc_now,write_json

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def jsonl(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def files(p):return sorted(f for f in p.rglob('*') if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc')

def source_spot_checks(run):
    """Small source-based development checks, not gold matching or human sign-off."""
    observations=[]
    path=run/'s2/IS1008a.p2.json'
    if path.exists():
        p2=load(path);events=jsonl(run/'s2/IS1008a.stages.jsonl')
        for e in events:
            if e.get('stage')=='select' and e.get('event')=='cap':
                for dropped in e['dropped']:
                    if 'slides' not in dropped['item']['text'].lower():continue
                    observations.append({'id':'IS1008a_slides_cap','severity':'QUALITY_GAP',
                        'finding':'IS1008a 的 slides Action 已通过抽取、合并与模型选择，却因 priority=1、Action 上限为 5，被排第 7 而截掉。',
                        'dropped':dropped,'stage_trace_seq':e['seq'],'source_trace':'run/s2/IS1008a.stages.jsonl'})
        for i in p2['items']:
            contradiction=[r for r in i['initial_context']['observed_dialogue'] if "don't think necessarily that I'm in a coordinating position" in r['text']]
            if 'coordinate usability testing' in i['item_text'] and contradiction and i['recordable']:
                observations.append({'id':'IS1008a_coordination_claim','severity':'NEEDS_SEMANTIC_REVIEW',
                    'finding':'IS1008a 的 UI 任务仍写成负责协调，但已交付的原文否认处于协调者位置。owner/status 待确认没有消除正文中的这一限定，需语义复核。',
                    'item_key':i['item_key'],'item_text':i['item_text'],'state':i['state'],'recordable':i['recordable'],
                    'statement_check':i['checks']['statement'],'source_quotes':contradiction,'source_p2':'run/s2/IS1008a.p2.json'})
    path=run/'s2/TS3006a.p2.json'
    if path.exists():
        items=load(path)['items']
        for i in items:
            if i['item_text']=='Industrial Designer will produce a working design.' and not i['recordable']:
                observations.append({'id':'TS3006a_ID_task_quarantined','severity':'SUSPECTED_P2_OVERBLOCKING',
                    'finding':'TS3006a 中 PM 分配 working design，ID 回应 True；statement 已通过，但 conflict 以“未发现矛盾、证据有限”为由保持 uncertain。Agent 因缺少 deadline 选择澄清，任务未进入主记录，疑似过度拦截。',
                    'item_key':i['item_key'],'state':i['state'],'recordable':i['recordable'],
                    'termination_reason':i['termination_reason'],'checks':i['checks'],
                    'source_quotes':i['initial_context']['observed_dialogue'],'source_p2':'run/s2/TS3006a.p2.json'})
        independent=['Set selling price at 25 Euros.','Sales target of 50 million Euros.','Production costs equal to half the selling price.']
        observations.append({'id':'TS3006a_independent_decisions','severity':'POSITIVE_CHECK',
            'finding':'TS3006a 的售价、销售额目标、生产成本已分别保留为三条 Decision。',
            'all_three_recordable':all(any(i['item_text']==text and i['recordable'] for i in items) for text in independent),
            'texts':independent,'source_p2':'run/s2/TS3006a.p2.json'})
    path=run/'s2/TS3006b.p2.json'
    if path.exists():
        items=load(path)['items'];links=[link for i in items if i['recordable'] for link in i['fields'].get('related_items',[])]
        if not links:
            observations.append({'id':'TS3006b_no_published_history_link','severity':'COVERAGE_GAP',
                'finding':'TS3006b 实际检索了旧条目并读取对话，但最终没有发布跨会议关系；此前人工确认的 TV＋video → 仅 TV 范围更新，本次没有重现为最终关联。读取范围与本轮历史文件哈希仍通过检查。',
                'published_links':links,'scope_items':[{'item_key':i['item_key'],'text':i['item_text'],'relation_check':i['checks']['relation'],'state':i['state'],'recordable':i['recordable']} for i in items if 'TV use only' in i['item_text']],
                'source_p2':'run/s2/TS3006b.p2.json'})
    return {'reviewer':'Codex source spot checks; no new human sign-off','gold_used':False,'observations':observations}

def inspect_probe(root):
    path=root.parent/'revision-probe-online-v1'
    if not (path/'run.json').exists():return {'status':'NOT_RUN'}
    run=load(path/'run.json');item=load(path/'outcome.json')
    revisions=[e for e in item['history'] if e.get('outcome')=='revised_requires_recheck']
    observed={r['da_id']:r for r in item['initial_context']['observed_dialogue']}
    for event in item['history']:
        for row in event.get('tool_result',{}).get('results',[]):
            if 'da_id' in row:observed[row['da_id']]=row
        if event.get('outcome')=='revised_requires_recheck':
            args=event['raw_tool_input']
            assert deletion(event['before_text'],args['remove_suffix'],args['evidence_da_ids'],observed,'IS1008a')==event['after_text']
    rechecks=[e for e in item['history'] if revisions and e.get('outcome')=='checked' and e['step']>revisions[0]['step'] and e.get('assessed_text')==item['item_text']]
    success=bool(len(revisions)==1 and rechecks and item['recordable'] and 'Friday' not in item['item_text'] and item['fields']['deadline'] is None)
    assert success==(run['status']=='REPAIRED_AND_RECHECKED')
    assert run['control']['attempted_calls']<=80 and run['control']['charged_tokens']<=1000000
    assert run['driver_sha256']==sha(root.parent/'probe_limited_revision.py')
    return {'status':run['status'],'kind':'CONTROLLED_FAULT_ON_REAL_DIALOGUE',
        'included_in_natural_case_counts':False,'published_as_main_meeting_output':False,
        'api_calls':run['actual_api_usage']['calls'],'known_tokens':run['actual_api_usage']['in']+run['actual_api_usage']['out'],
        'valid_revisions':len(revisions),'fresh_rechecks':len(rechecks),'state':item['state'],
        'recordable':item['recordable'],'final_text':item['item_text'],'deadline':item['fields']['deadline'],
        'evidence':[observed[d] for e in revisions for d in e['evidence_da_ids']],
        'artifact_directory':'../revision-probe-online-v1'}

def inspect(root):
    study=load(root/'study.json');run=root/'run'
    for name,value in study['expected_config'].items():setattr(config,name,value)
    config.DATA=BUNDLE/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    rows=[];cases=[];errors=[]
    for mid in study['meetings']:
        result_path=run/(mid+'.result.json');meta_path=run/'s2'/(mid+'.run.json')
        if not result_path.exists():
            rows.append({'meeting_id':mid,'status':'NOT_RUN_OR_LAUNCHER_DID_NOT_SAVE_RESULT','native_metadata_exists':meta_path.exists()})
            errors.append(mid+': missing CLI result');continue
        result=load(result_path);meta=load(meta_path)
        events=jsonl(run/'s2'/(mid+'.stages.jsonl'))
        c=result['runtime_control'];attempts=c['attempts']
        stage_counts={s:sum(a['stage']==s for a in attempts) for s in sorted({a['stage'] for a in attempts})}
        cached=[{'stage':e['stage'],'event':e['event']} for e in events if
            e.get('stage') in ('extract','consolidate','decision_consolidate','select') and e.get('source')=='cache']
        cached_actions=sum(e.get('cached_samples',0) for e in events if e.get('stage')=='extract' and e.get('event')=='action_union')
        reserved=any(e.get('event')=='captured_upstream_budget_reserved' for e in events)
        row={'meeting_id':mid,'status':result['status'],'failed_stage':result.get('failed_stage'),'reason':result.get('reason'),
             'seconds':result.get('seconds',meta.get('elapsed_seconds')),'stage_seconds':result.get('stage_seconds',{}),
             'api_attempts':c['attempted_calls'],'known_tokens':sum(a.get('input_tokens',0)+a.get('output_tokens',0) for a in attempts if a.get('usage_known')),
             'charged_tokens':c['charged_tokens'],'unknown_usage_tokens':c['unknown_usage_tokens'],
             'model_attempts_by_stage':stage_counts,'historical_response_cache_hits':len(cached)+cached_actions,
             'historical_budget_reserved':reserved,'limits':c['limits'],'max_inflight_seen':c['max_inflight_seen']}
        assert meta['model']==study['expected_model'],mid+': model changed'
        assert meta['config']==study['expected_config'],mid+': config changed'
        txt=BUNDLE/'inputs/transcripts'/(mid+'.txt')
        assert meta['input']['sha256']==sha(txt),mid+': input differs from frozen TXT'
        assert load(Path(meta['checkpoints']['parsed']))==parse_transcript_file(txt),mid+': parsed checkpoint differs from TXT'
        assert [e['seq'] for e in events]==list(range(len(events))),mid+': missing/duplicate stage trace event'
        frozen=load(BUNDLE/'study.json')['runtime']['code']['source_sha256']
        assert meta['code']['source_sha256']==frozen,mid+': source changed'
        assert Path(meta['cache_directory']).resolve()==(run/'_cache').resolve(),mid+': wrong cache directory'
        assert not cached and cached_actions==0 and not reserved,mid+': reused historical cache/cost'
        assert c['limits']['calls']==80 and c['limits']['tokens']==1000000 and c['limits']['meeting_seconds']==1200
        assert c['attempted_calls']<=80 and c['charged_tokens']<=1000000 and c['max_inflight_seen']<=4
        if result['status']!='COMPLETED':
            row['failure']=result.get('failure');row['saved_checkpoints']=list(meta['checkpoints'])
            row['partial_artifact']=result.get('partial_artifact')
            errors.append(mid+': '+result['status']);rows.append(row);continue
        record=load(Path(result['output']));p2=load(Path(result['p2_artifact']));questions=load(Path(result['clarifications_artifact']))
        store=Store(mid,allow_prior=True,run_dir=run/'s2')
        for prior in meta.get('prior_context',[]):
            assert prior['meeting_id'] in store.visible_ids('prior')
            assert sha(run/'s2'/(prior['meeting_id']+'.json'))==prior['record_sha256'],mid+': prior record not from this fresh run'
            assert sha(BUNDLE/'inputs/transcripts'/(prior['meeting_id']+'.txt'))==prior['transcript_sha256']
        validation=check(record,store)+validate_outputs(p2,questions,record,store)
        report=Path(result['output']).with_name(mid+'_report.md');report_text=report.read_text(encoding='utf-8')
        for key,path in [('output_sha256',Path(result['output'])),('report_sha256',report),('p2_sha256',Path(result['p2_artifact'])),('clarifications_sha256',Path(result['clarifications_artifact']))]:
            assert sha(path)==result[key],mid+': artifact hash mismatch'
        assert events[-1]['event']=='finished' and events[-1]['status']=='COMPLETED'
        agent_trace=jsonl(run/'s2'/(mid+'.trace.jsonl'))
        assert agent_trace or not p2['items']
        delivered_dialogue=[];lookup_counts={}
        for item in p2['items']:
            assert item['initial_context']['earlier_meetings']==store.visible_ids('prior')
            delivered_dialogue.extend(item['initial_context']['observed_dialogue'])
            for e in item['history']:
                if e.get('outcome')=='lookup':
                    lookup_counts[e['action']]=lookup_counts.get(e['action'],0)+1
                    delivered_dialogue.extend(r for r in e.get('tool_result',{}).get('results',[]) if 'da_id' in r)
        assert all(r['da_id'].split('.')[0] in store.visible_ids() for r in delivered_dialogue),mid+': out-of-scope delivered dialogue'
        mandatory=['extract','consolidate','decision_consolidate','select','p2_check','abstract']
        missing=[s for s in mandatory if not stage_counts.get(s,0)]
        row.update(validation_errors=validation,missing_mandatory_model_stages=missing,
            record_counts={t:sum(r['type']==t for r in record) for t in ('actions','decisions','problems','abstract')},
            p2_items=len(p2['items']),accepted=sum(i['state']=='accepted' for i in p2['items']),
            pending=sum(i['state']=='pending' for i in p2['items']),recordable=sum(i['recordable'] for i in p2['items']),
            clarification_requests=len(questions['requests']),all_questions_in_report=all(q['question'] in report_text for q in questions['requests']),
            stage_trace_events=len(events),agent_trace_events=len(agent_trace),
            input_txt_and_parsed_checkpoint_verified=True,checkpoints_saved=len(meta['checkpoints']),
            prior_context=meta.get('prior_context',[]),actual_lookup_tools=lookup_counts,
            delivered_dialogue_scope_verified=True)
        errors += [mid+': '+e for e in validation]
        if missing:errors.append(mid+': mandatory API stage not exercised: '+','.join(missing))
        if not row['all_questions_in_report']:errors.append(mid+': report omitted clarification question')
        published={r['id']:r for r in record if r['type']!='abstract'}
        for item in p2['items']:
            relevant=[e for e in item['history'] if e.get('action')=='revise_statement' or e.get('decision')=='revise_statement']
            if not relevant:continue
            accepted=[e for e in relevant if e.get('outcome')=='revised_requires_recheck']
            witness={'meeting_id':mid,'item_key':item['item_key'],'state':item['state'],'recordable':item['recordable'],
                'initial_text':item['initial_context']['item']['text'],'final_text':item['item_text'],
                'events':relevant,'valid_revisions':len(accepted),'record_id':item['record_id'],
                'rechecked_after_revision':False,'published_after_recheck':False}
            seen={r['da_id']:r for r in item['initial_context']['observed_dialogue']}
            for event in item['history']:
                for source in event.get('tool_result',{}).get('results',[]):
                    if 'da_id' in source:seen[source['da_id']]=source
                if event.get('outcome')=='revised_requires_recheck':
                    args=event['raw_tool_input']
                    revised=deletion(event['before_text'],args['remove_suffix'],args['evidence_da_ids'],seen,mid)
                    assert revised==event['after_text'] and len(accepted)==1
                    witness['source_quotes']=[seen[d] for d in args['evidence_da_ids']]
                    rechecks=[e for e in item['history'] if e.get('outcome')=='checked' and e['step']>event['step'] and e.get('assessed_text')==revised]
                    witness['rechecked_after_revision']=bool(rechecks)
                    witness['published_after_recheck']=bool(item['recordable'] and rechecks and published.get(item['record_id'],{}).get('text')==revised)
                    if item['recordable']:assert witness['published_after_recheck']
            cases.append(witness)
        rows.append(row)
    protected=load(root/'protected_before.json')
    for name,value in protected.items():assert sha(REPO/name)==value,'Protected file changed: '+name
    source_study=load(BUNDLE/'study.json')
    for name,value in source_study['runtime']['code']['source_sha256'].items():assert sha(REPO/name)==value,'Working source changed: '+name
    for name,value in load(BUNDLE/'freeze_manifest.json')['artifact_sha256'].items():assert sha(BUNDLE/name)==value,'Frozen artifact changed: '+name
    prefix=['git','-c','safe.directory='+REPO.as_posix()]
    assert subprocess.check_output(prefix+['rev-parse','HEAD'],text=True).strip()==study['commit']
    assert not subprocess.check_output(prefix+['diff','--cached','--name-only'],text=True).strip()
    assert subprocess.check_output(prefix+['branch','--show-current'],text=True).strip()=='agent-development'
    successes=[w for w in cases if w['published_after_recheck']]
    revision_status='DEMONSTRATED_RECHECKED_AND_PUBLISHED' if successes else ('ATTEMPTED_WITHOUT_VERIFIED_PUBLICATION' if cases else 'NOT_EXERCISED_IN_NATURAL_RUN')
    spot=source_spot_checks(run)
    write_json(root/'source_spot_checks.json',spot)
    probe=inspect_probe(root)
    summary={'created_at':utc_now(),'commit':study['commit'],'execution_kind':study['execution_kind'],
        'engineering_result':'PASSED' if not errors else 'FAILED_OR_INCOMPLETE','errors':errors,'meetings':rows,
        'limited_revision':{'status':revision_status,'cases':len(cases),'valid_revisions':sum(w['valid_revisions'] for w in cases),'published_successes':len(successes)},
        'supplementary_revision_probe':probe,
        'totals':{'api_attempts':sum(r.get('api_attempts',0) for r in rows),'known_tokens':sum(r.get('known_tokens',0) for r in rows),'meeting_seconds':round(sum(r.get('seconds',0) for r in rows),1)},
        'source_spot_checks':spot,
        'release_acceptance':'NOT_SIGNED_OFF' if not successes or any(o['severity']!='POSITIVE_CHECK' for o in spot['observations']) or errors else 'ENGINEERING_CHECKS_AND_REVISION_WITNESS_PASSED',
        'source_and_frozen_inputs_unchanged':True,'protected_files_unchanged':len(protected),
        'gold_scoring_performed':False,'not_committed':True}
    write_json(root/'summary.json',summary);write_json(root/'limited_revision_cases.json',{'natural_cases_only':True,'cases':cases})
    lines=['# 冻结版空缓存完整试跑','',f"固定提交：`{study['commit']}`。原冻结入口直接运行，模型和配置保持原样。",'',
        '本次从 TXT 开始；没有复制任何模型响应缓存、归一化候选或旧历史记录。每场完整 80 次/100 万 token/1,200 秒预算，不扣历史预留。后续会议只读取本轮实际生成的更早记录。',
        '',f"运行与产物结构检查：**{summary['engineering_result']}**。交付验收：**{summary['release_acceptance']}**；本次未读取 gold 打分，不属于最终 evaluation。",'',
        '| 会议 | 状态 | 秒 | 实际尝试 | 已知 token | 计入 token | 可记录/输入 | 待确认问题 |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |']
    for row in rows:
        lines.append(f"| {row['meeting_id']} | {row['status']} | {row.get('seconds','—')} | {row.get('api_attempts','—')} | {row.get('known_tokens','—')} | {row.get('charged_tokens','—')} | {row.get('recordable','—')}/{row.get('p2_items','—')} | {row.get('clarification_requests','—')} |")
    lines+=['','已知 token 是 API 返回的 input+output；计入 token 还可能包含失败请求的未知用量保守预留，不能当成供应商账单。','',
        '## 各阶段真实调用','', '| 会议 | 抽取 | 合并 | Decision 重合并 | 选择 | P2 检查 | 自主工具决策 | 摘要 |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:
        s=row.get('model_attempts_by_stage',{})
        lines.append('| '+row['meeting_id']+' | '+' | '.join(str(s.get(k,0)) for k in ['extract','consolidate','decision_consolidate','select','p2_check','agent','abstract'])+' |')
    lines+=['','## P2 有限修正','',f"状态：**{revision_status}**。有效修正 {summary['limited_revision']['valid_revisions']} 次，重新检查后发布 {len(successes)} 次。",'',
        '“没有出现成功修正”不能用流程完成或单元测试通过替代。自然数据未触发时，只能说本次未覆盖该路径；被拒绝的参数/引用只证明护栏拦截，不能计作自主修正成功。',
        '每个实际案例的原句、参数、证据、改后文本及后续检查见 [limited_revision_cases.json](limited_revision_cases.json)。','',
        '## 独立有限修正诊断','',f"结果：**{probe['status']}**。它与上面的自然完整流程分别保存；不计入自然案例、F1 或最终主记录。",'']
    if probe['status']!='NOT_RUN':
        lines += [f"对真实 slides 证据注入无依据的 `by Friday` 后，使用相同冻结源码、模型、配置和预算，Agent 自主选择工具。实际 {probe['api_calls']} 次调用，{probe['known_tokens']} token。",
            '',f"改后句子：`{probe['final_text']}`。有效删除 {probe['valid_revisions']} 次，随后重新检查 {probe['fresh_rechecks']} 次；结果为 `{probe['state']}`，`recordable={probe['recordable']}`，deadline 为 null。",
            '', '这证明修正及复查路径能工作，且未把未确定的时间写回；它不证明自然输入下的调用稳定性。受控诊断的主任务没有发布成新的会议输出。',
            '', '[完整输入、请求、trace、结果](../revision-probe-online-v1/)。首次沙箱尝试因 WinError 10013 无法联网，保存在 ../revision-probe-v1/，不计入在线成功样本。','']
    lines += [
        '## 基于原文的内容抽查','',
        '以下是 Codex 对原文和阶段产物的检查，不代表新增的人工确认。细节及证据见 [source_spot_checks.json](source_spot_checks.json)。','']
    lines+=['- **'+o['severity']+'** '+o['finding'] for o in spot['observations']]
    lines+=['',
        '## 产物与边界','',
        '- run/s2/*.json：主记录；同名 *_report.md：可读报告。',
        '- run/s2/*.p2.json 与 *.clarifications.json：检查、待确认状态及问题对应关系。',
        '- run/s2/*.stages.jsonl / *.trace.jsonl：阶段、真实 API 尝试和 Agent 决策；_attempts/ 保存原始 checkpoint。',
        '- study.json：本次空缓存条件、固定代码/模型/配置；summary.json：结构验证和预算结果。',
        '- 本次只新增试跑产物与核查脚本；UI、标注文档和冻结源码未改动，未 commit。']
    if errors:lines+=['','## 未通过项','']+['- '+e for e in errors]
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_json(root/'manifest.json',{'format_version':1,'artifact_sha256':{p.relative_to(root).as_posix():sha(p) for p in files(root) if p!=root/'manifest.json'}})
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    inspect(p.parse_args().root.resolve())
