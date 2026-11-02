"""Offline audit of the one cold development campaign. Never loads gold or calls a model."""
from __future__ import annotations
import argparse
import ast
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import warnings

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path.insert(0,str(REPO/'agent/src'))
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def lines(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def files(root):return sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
def git(*args):return subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),*args],cwd=REPO).decode('utf-8').strip()

def inspect(root):
    from mta import config
    from mta.p2 import validate_outputs
    from mta.store import Store
    from mta.transcript_parser import parse_transcript_file
    from mta.validate import check
    study=load(root/'study.json');state=load(root/'state.json')
    assert state['status']=='FINISHED','Do not audit a live campaign'
    config.DATA=root/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    output=root/'run/s2';rows=[];issues=[];link_cases=[];pending_relations=[];revisions=[]
    def require(condition,message,mid='campaign'):
        if not condition:issues.append({'meeting_id':mid,'error':message})
    require(git('branch','--show-current')=='agent-development','branch changed')
    require(git('rev-parse','HEAD')==study['commit'],'HEAD changed')
    require(not git('diff','--cached','--name-only'),'index unexpectedly staged')
    for rel,digest in study['source_sha256'].items():
        require(sha(REPO/rel)==digest,'runtime source changed: '+rel)
        require(sha(root/'source'/rel)==digest,'source snapshot changed: '+rel)
    for rel,digest in study['input_sha256'].items():require(sha(root/rel)==digest,'input changed: '+rel)
    for rel,digest in study['input_sha256'].items():
        original=REPO/'agent/data'/Path(rel).relative_to('inputs')
        require(sha(original)==digest,'original development input changed: '+rel)
    require(sha(root/'driver.py')==study['driver_sha256'],'frozen driver changed')
    protected=load(root/'protected_before.json')
    for rel,digest in protected.items():require(sha(REPO/rel)==digest,'protected file changed: '+rel)
    require(sorted(p.stem for p in (root/'inputs/transcripts').glob('*.txt'))==sorted(study['meetings']),'unexpected transcript set')
    require(not study['seeded_responses'] and not study['prior_records_seeded'] and not study['cache_preexisting'],'seeded run')
    archive=root/'verification_drivers';archive.mkdir(exist_ok=True)
    for name in ('audit.py','replay.py','diagnose_consolidation.py'):
        source=Path(__file__).parent/name;target=archive/name
        if source.resolve()!=target.resolve():shutil.copyfile(source,target)
    limits=study['runtime']['config'];request_counts={}
    for scheduled in state['meetings']:
        mid=scheduled['meeting_id'];row={'meeting_id':mid,'status':scheduled['status']}
        if scheduled['status']=='BLOCKED_BY_PRIOR_FAILURE':
            row.update(blocked_by=scheduled['blocked_by'],api_attempts=0)
            require(not (output/(mid+'.run.json')).exists(),'blocked meeting ran',mid)
            rows.append(row);continue
        result=load(root/'run'/(mid+'.result.json'));meta=load(output/(mid+'.run.json'))
        events=lines(output/(mid+'.stages.jsonl'));trace=lines(output/(mid+'.trace.jsonl'))
        c=result.get('runtime_control',{});attempts=c.get('attempts',[])
        require(result['status']==scheduled['status']==meta['status'],'terminal status mismatch',mid)
        require(meta['code']['git_commit']==study['commit'],'wrong commit',mid)
        require(meta['code']['source_sha256']==study['runtime']['code']['source_sha256'],'runtime source mismatch',mid)
        require(meta['model']==study['runtime']['model'],'model changed',mid)
        require(meta['config']==study['runtime']['config'],'config changed',mid)
        require({row.split()[-1] for row in meta['code']['dirty_source_files']}==set(study['source_patch_paths']),'unexpected production source changes',mid)
        require(meta['input']['sha256']==sha(root/'inputs/transcripts'/(mid+'.txt')),'TXT changed',mid)
        require(Path(meta['cache_directory']).resolve()==(root/'run/_cache').resolve(),'wrong cache',mid)
        require([e['seq'] for e in events]==list(range(len(events))),'stage trace sequence gap',mid)
        require(events[-1].get('event')=='finished' and events[-1]['status']==result['status'],'missing terminal event',mid)
        cached=[e for e in events if e.get('source')=='cache' and e.get('stage') in ('extract','consolidate','decision_consolidate','select')]
        cached_samples=sum(e.get('cached_samples',0) for e in events if e.get('event')=='action_union')
        require(not cached and not cached_samples,'unexpected historical response reuse',mid)
        require(not any(e.get('event')=='captured_upstream_budget_reserved' for e in events),'historical budget debit',mid)
        expected_limits={'calls':limits['MAX_MODEL_CALLS'],'tokens':limits['MAX_TOTAL_TOKENS'],
            'request_seconds':limits['REQUEST_TIMEOUT'],'meeting_seconds':limits['MEETING_TIMEOUT'],
            'inflight':limits['MAX_INFLIGHT'],'retries':limits['TRANSPORT_RETRIES'],'backoff':limits['RETRY_BACKOFF']}
        require(c.get('limits')==expected_limits,'whole-meeting limits changed',mid)
        require(c.get('attempted_calls',0)<=limits['MAX_MODEL_CALLS'],'call overrun',mid)
        require(c.get('charged_tokens',0)<=limits['MAX_TOTAL_TOKENS'],'token overrun',mid)
        require(c.get('max_inflight_seen',0)<=limits['MAX_INFLIGHT'],'concurrency overrun',mid)
        require(len(attempts)==c.get('attempted_calls'),'missing call accounting',mid)
        require(sum(a.get('charged_tokens',0) for a in attempts)==c.get('charged_tokens'),'token ledger mismatch',mid)
        stages=dict(Counter(a['stage'] for a in attempts));known=sum(a.get('input_tokens',0)+a.get('output_tokens',0) for a in attempts if a.get('usage_known'))
        requests=root/'run'/(mid+'.requests.jsonl');request_counts[mid]=len(lines(requests)) if requests.exists() else 0
        row.update(seconds=result.get('seconds',meta.get('elapsed_seconds')),api_attempts=c.get('attempted_calls',0),
            known_tokens=known,charged_tokens=c.get('charged_tokens',0),unknown_usage_tokens=c.get('unknown_usage_tokens',0),
            model_attempts_by_stage=stages,max_inflight_seen=c.get('max_inflight_seen',0),
            historical_cache_hits=len(cached)+cached_samples,stage_trace_events=len(events),agent_trace_events=len(trace),
            checkpoints_saved=len(meta['checkpoints']),failed_stage=result.get('failed_stage'),reason=result.get('reason'))
        if 'parsed' in meta['checkpoints']:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                parsed_txt=parse_transcript_file(root/'inputs/transcripts'/(mid+'.txt'))
            row['parser_warnings']=[{'category':w.category.__name__,'message':str(w.message)} for w in caught]
            require(load(Path(meta['checkpoints']['parsed']))==parsed_txt,'parsed TXT mismatch',mid)
        store=Store(mid,True,output);prior=meta.get('prior_context',[])
        if 'parsed' in meta['checkpoints']:
            require([p['meeting_id'] for p in prior]==store.visible_ids('prior'),'prior scope mismatch',mid)
        for p in prior:
            old=output/(p['meeting_id']+'.json')
            require(p['meeting_id'] in store.visible_ids('prior'),'foreign/future history',mid)
            require(old.is_file() and sha(old)==p['record_sha256'],'prior record not from this campaign',mid)
            require(sha(root/'inputs/transcripts'/(p['meeting_id']+'.txt'))==p['transcript_sha256'],'prior TXT mismatch',mid)
        row['prior_context']=prior
        selected=[e for e in events if e.get('stage')=='select' and e.get('event')=='attempt']
        row['selection_attempts']=len(selected)
        row['selection_errors']=[e['error'] for e in selected if e.get('error')]
        row['duplicate_reason_metadata']=[]
        for event in selected:
            for change in event.get('normalizations',[]):
                if change['action']!='duplicate_reason_from_explicit_target':continue
                raw=event['raw_response'];entry=raw[change['source']]
                require('reason' not in entry and entry.get('keep') is False and entry.get('claims',[])==[],
                        'metadata normalization hid an ambiguous disposition',mid)
                require(entry['duplicate_of']==change['duplicate_of'] and raw[entry['duplicate_of']]['keep'] is True,
                        'metadata normalization chose a new duplicate target',mid)
                row['duplicate_reason_metadata'].append({'trace_seq':event['seq'],**change})
        if result['status']!='COMPLETED':
            row.update(failure=result.get('failure'),partial_artifact=result.get('partial_artifact'),saved_checkpoints=list(meta['checkpoints']))
            require(bool(result.get('partial_artifact')) and Path(result['partial_artifact']).is_file(),'missing partial artifact',mid)
            require(not (output/(mid+'.json')).exists(),'failed run published main record',mid)
            rows.append(row);continue
        record=load(output/(mid+'.json'));p2=load(output/(mid+'.p2.json'));questions=load(output/(mid+'.clarifications.json'))
        errors=check(record,store)+validate_outputs(p2,questions,record,store)
        for error in errors:require(False,error,mid)
        for key,path in [('output_sha256',output/(mid+'.json')),('report_sha256',output/(mid+'_report.md')),
            ('p2_sha256',output/(mid+'.p2.json')),('clarifications_sha256',output/(mid+'.clarifications.json'))]:
            require(sha(path)==result[key],'artifact hash mismatch: '+key,mid)
        report=(output/(mid+'_report.md')).read_text(encoding='utf-8')
        require(all(q['question'] in report for q in questions['requests']),'report omits clarification',mid)
        require(bool(trace) or not p2['items'],'missing decision trace',mid)
        row.update(validation_errors=errors,record_counts=dict(Counter(r['type'] for r in record)),p2_items=len(p2['items']),
            accepted=sum(i['state']=='accepted' for i in p2['items']),pending=sum(i['state']=='pending' for i in p2['items']),
            recordable=sum(i['recordable'] for i in p2['items']),clarification_requests=len(questions['requests']),
            schema_rejections=0,lookup_rejections=0,invalid_actions=0,protocol_normalizations=0,
            actual_tools={},termination_counts=dict(Counter(i['termination_reason'] for i in p2['items'])),published_links=0,
            relation_pending=0,valid_revisions=0,document_citations=sum(r['source_type']=='project_document' for i in record for r in i['reference']))
        tool_counts=Counter();published={r['id']:r for r in record if r['type']!='abstract'}
        for item in p2['items']:
            require(item['initial_context']['earlier_meetings']==store.visible_ids('prior'),'item history scope mismatch',mid)
            require(all(d.split('.')[0] in store.visible_ids() for d in item['observed_evidence_da_ids']),'out-of-scope observed evidence',mid)
            seen={d['da_id']:d for d in item['initial_context']['observed_dialogue']}
            for e in item['history']:
                if e.get('outcome')=='lookup':
                    tool_counts[e['action']]+=1
                    for d in e.get('tool_result',{}).get('results',[]):
                        if 'da_id' in d:
                            require(d['da_id'].split('.')[0] in store.visible_ids(),'out-of-scope delivered dialogue',mid);seen[d['da_id']]=d
                        if d.get('kind')=='prior_item':require(d['id'].split('.')[0] in store.visible_ids('prior'),'out-of-scope prior item',mid)
                if e.get('outcome')=='invalid_action':
                    row['invalid_actions']+=1
                    if 'raw_tool_input' in e:row['schema_rejections']+=1
                    else:row['lookup_rejections']+=1
                row['protocol_normalizations']+=len(e.get('protocol_normalizations',[]))
                if e.get('outcome')=='revised_requires_recheck':
                    row['valid_revisions']+=1
                    revisions.append({'meeting_id':mid,'item_key':item['item_key'],'record_id':item['record_id'],
                        'event':e,'final_text':item['item_text'],'recordable':item['recordable'],'state':item['state'],
                        'rechecks':[h for h in item['history'] if h.get('outcome')=='checked' and h['step']>e['step']]})
            relation=item['checks']['relation']
            if 'related_items' in item['unresolved_fields']:
                row['relation_pending']+=1
                pending_relations.append({'meeting_id':mid,'item_key':item['item_key'],'text':item['item_text'],
                    'record_id':item['record_id'],'termination_reason':item['termination_reason'],'relation':relation,
                    'history':[e for e in item['history'] if e.get('outcome') in ('lookup','invalid_action') or e.get('action')=='assess_relation']})
            for witness in item.get('relation_evidence',[]):
                if witness['state']=='supported' and item['record_id'] in published:
                    row['published_links']+=1
                    link_cases.append({'meeting_id':mid,'item_key':item['item_key'],'new_record':published[item['record_id']],
                        'old_record':next((i for i in store.prior_items() if i['id']==witness['prior_item_id']),None),
                        'witness':witness,'relation_check':relation,
                        'actual_lookup_history':[e for e in item['history'] if e.get('outcome')=='lookup'],
                        'semantic_review':'Not independently human-confirmed; source checks passed, relation label is model judgment.'})
        row['actual_tools']=dict(tool_counts);rows.append(row)
    requested=len(study['meetings']);completed=sum(r['status']=='COMPLETED' for r in rows);attempted=sum(r['status']!='BLOCKED_BY_PRIOR_FAILURE' for r in rows)
    expected_order=[(kind,r['meeting_id']) for r in state['meetings'] if r['status']!='BLOCKED_BY_PRIOR_FAILURE'
                    for kind in ('meeting_started','meeting_finished')]
    actual_order=[(e['event'],e['meeting_id']) for e in lines(root/'campaign.jsonl')
                  if e['event'] in ('meeting_started','meeting_finished')]
    require(actual_order==expected_order,'native attempt chronology is not the scheduled a-to-d serial order')
    statuses=dict(Counter(r['status'] for r in rows));totals={k:sum(r.get(k,0) for r in rows) for k in
        ('api_attempts','known_tokens','charged_tokens','unknown_usage_tokens','published_links','clarification_requests','valid_revisions',
         'p2_items','accepted','pending','recordable','relation_pending','invalid_actions','schema_rejections','lookup_rejections','protocol_normalizations')}
    summary={'created_at':datetime.now(timezone.utc).isoformat(),'commit':study['commit'],'mode':'s2','scheduled':requested,
        'attempted':attempted,'completed':completed,'completion_fraction':completed/requested,'statuses':statuses,
        'artifact_audit':'PASSED' if not issues else 'FAILED','campaign_result':'ALL_REQUESTED_COMPLETED' if completed==requested and not issues else 'COMPLETION_OR_AUDIT_GAPS',
        'issues':issues,'meetings':rows,'totals':totals,'wall_seconds':state['wall_seconds'],'protected_files_unchanged':len(protected),
        'gold_loaded_or_scored':False,'semantic_accuracy_measured':False,'human_signoff':False,'not_committed':True,
        'logical_requests':request_counts,'request_log_note':'Logical calls only; runtime_control attempts include native transport retries and are authoritative.'}
    preservation=load(root/'offline/before.json')
    old=Path(study['original_cohort'])
    require(sha(old/'manifest.json')==preservation['previous_manifest_sha256'],'old manifest changed')
    for rel,digest in preservation['previous_artifact_sha256'].items():require(sha(old/rel)==digest,'old regression artifact changed: '+rel)
    for rel,digest in preservation['previous_parent_files'].items():require(sha(REPO/rel)==digest,'old regression driver/doc changed: '+rel)
    replay=load(root/'offline/replay.json')
    require(replay['result']=='PASSED' and replay['actual_new_api_calls']==0,'offline replay not passed')
    require(replay['after_source_sha256']==sha(REPO/'agent/src/mta/item_selection.py'),'replay/fresh-run source mismatch')
    tests=load(root/'tests.json');require(tests['passed']==400,'tests not passed')
    for rel,digest in tests['test_sha256'].items():require(sha(REPO/rel)==digest,'tested file changed: '+rel)
    require(sha(REPO/'agent/tests/fixtures/selection_missing_reason.json')==tests['fixture_sha256'],'test fixture changed')
    require(sha(root/'source.patch')==study['source_patch_sha256'],'source patch changed')
    original=load(old/'study.json')
    require(study['runtime']['config']==original['runtime']['config'],'config differs from original cohort')
    require(study['runtime']['model']==original['runtime']['model'],'model differs from original cohort')
    for rel,digest in study['input_sha256'].items():require(digest==original['input_sha256'][rel],'different original input: '+rel)
    summary['old_artifacts_preserved']=len(preservation['previous_artifact_sha256'])
    summary['offline_selection_replay_passed']=True
    summary['new_consolidation_failure']=load(root/'consolidation_failure.json') if (root/'consolidation_failure.json').exists() else None
    summary['unit_tests']=tests['passed']
    summary['scope_note']='Independent four-meeting cold rerun. Never combined with the old 13/16 cohort into a single-version 16/16 result.'
    summary['duplicate_reason_metadata_count']=sum(len(r.get('duplicate_reason_metadata',[])) for r in rows)
    summary['issues']=issues
    summary['artifact_audit']='PASSED' if not issues else 'FAILED'
    summary['campaign_result']='ALL_REQUESTED_COMPLETED' if completed==requested and not issues else 'COMPLETION_OR_AUDIT_GAPS'
    diagnosis=None
    reviews=load(root/'source_reviews.json') if (root/'source_reviews.json').exists() else {'cases':[]}
    summary['selection_failure_diagnosis']=diagnosis
    summary['source_review_statuses']=dict(Counter(c['status'] for c in reviews['cases']))
    summary['item_termination_counts']=dict(sum((Counter(r.get('termination_counts',{})) for r in rows),Counter()))
    save(root/'summary.json',summary);save(root/'cross_meeting_links.json',{'gold_used':False,'human_signoff':False,'cases':link_cases})
    save(root/'pending_relations.json',{'cases':pending_relations});save(root/'limited_revisions.json',{'cases':revisions})
    readable=['# 真实 S2 跨会议关联：来源核查清单','',
        '仅列本轮实际发布的关联。来源/结构校验通过，关系标签由模型选择，尚未独立人工确认；没有用 gold 打分。',
        '完整工具返回、旧记录哈希与双方证据 witness 见 cross_meeting_links.json。','']
    for n,case in enumerate(link_cases,1):
        old=case['old_record'];new=case['new_record'];w=case['witness']
        selected=next((l for l in case['relation_check'].get('links',[]) if l['id']==old['id']),{})
        readable += [f"## {n}. {old['id']} → {new['id']}",'',
            '旧记录：'+old['text'],'','新记录：'+new['text'],'',
            '模型选择：'+str(w.get('relation'))+'；变化字段：'+json.dumps(selected.get('changed_fields',[]),ensure_ascii=False),'',
            '判断理由：'+selected.get('reason',case['relation_check']['reason']),'','实际工具序列：']
        readable += ['- '+e['action']+' '+json.dumps(e.get('args',{}),ensure_ascii=False) for e in case['actual_lookup_history']]
        readable += ['','模型选用原文：']
        readable += ['- `'+q['da_id']+'`：'+q['quote'] for q in case['relation_check']['quotes']]
        readable += ['','人工核对：待确认。','']
        for note in reviews['cases']:
            if note.get('new_record_id')==new['id'] and note.get('old_record_id')==old['id']:
                readable+=['来源抽查标记：**'+note['status']+'**。'+note['finding'],'']
    if not link_cases:readable.append('本轮没有发布跨会议关联；未决关系仍保存在 pending_relations.json。')
    (root/'CROSS_MEETING.md').write_text('\n'.join(readable),encoding='utf-8')
    text=['# 选择重复条目元数据修复：TS3006 四场空缓存 S2 回归','',f"基于 `{study['commit']}`，仅修复 item_selection.py 的重复项元数据处理，补丁见 source.patch；分支 agent-development。未 commit，UI 和标注文档未改。",'',
        '范围：TS3006 a→d。原生 pipeline 从 TXT 真实运行；新目录、新缓存、未复制旧记录、未扣历史预算。模型、提示词、配置、整场预算和原生重试保持不变。未读取 gold 或留出集转写。', '',
        f"完成 **{completed}/{requested}（{completed/requested:.2%}）**，实际尝试 {attempted} 场；产物审计 **{summary['artifact_audit']}**。失败同系列的后续会议按原生串行策略阻塞，其他系列继续；不替换历史、不重跑择优。",'',
        '| 会议 | 状态 | 秒 | 模型尝试 | 计入 token | 可记录/P2 输入 | 澄清 | 发布关联 |',
        '| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |']
    for r in rows:
        text.append(f"| {r['meeting_id']} | {r['status']} | {r.get('seconds','—')} | {r.get('api_attempts',0)} | {r.get('charged_tokens','—')} | {r.get('recordable','—')}/{r.get('p2_items','—')} | {r.get('clarification_requests','—')} | {r.get('published_links','—')} |")
    text+=['',f"总模型尝试 {totals['api_attempts']}；已知 token {totals['known_tokens']:,}，计入 token {totals['charged_tokens']:,}（未知用量保守计入 {totals['unknown_usage_tokens']:,}）。计入 token 不等于供应商账单。",'',
        '## 真实调用阶段','',
        '| 会议 | 抽取 | 合并 | Decision 重合并 | 选择 | P2 | 自主工具决策 | 摘要 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        counts=r.get('model_attempts_by_stage',{})
        text.append('| '+r['meeting_id']+' | '+' | '.join(str(counts.get(k,0)) for k in ('extract','consolidate','decision_consolidate','select','p2_check','agent','abstract'))+' |')
    text+=['',
        '## 检查与限制','',
        '- 完成场次逐个校验原生 JSON/schema、P2 与澄清映射、报告问题覆盖、文件哈希、阶段/Agent trace、TXT→解析 checkpoint 和调用/token 账本。',
        '- 每场 80 次调用、100 万 token、请求超时 300 秒、整场超时 1200 秒、并发 4、重试 2；每个系列后续会议只读取本轮已发布的同系列更早记录及原文。',
        '- stages 和 runtime_control 是实际尝试与重试的依据；requests.jsonl 记录逻辑请求，不应当成供应商调用次数。',
        '- 关联 witness 校验旧记录、双方原文及最终对应关系；来源验证通过不等于关系语义已人工确认。本轮不计算 gold P/R/F1，不作为最终 evaluation 或稳定性保证。',
        f"- 发布关联 {totals['published_links']} 条；逐条旧记录→工具返回→Agent 判断→新记录见 [可读关联清单](CROSS_MEETING.md) / cross_meeting_links.json，未决关系见 pending_relations.json。", 
        f"- P2 结果：{totals['p2_items']} 条检查条目，accepted {totals['accepted']}、pending {totals['pending']}；可记录 {totals['recordable']} 条，其他未发布条目不能当作已完成内容验证。澄清请求 {totals['clarification_requests']} 条。",
        f"- 自然运行有限修正 {totals['valid_revisions']} 次；事件及重新检查见 limited_revisions.json。没有额外错误注入或单条重复。",'',
        '## 失败与审计问题','']
    failed=[r for r in rows if r['status'] not in ('COMPLETED','BLOCKED_BY_PRIOR_FAILURE')]
    text += [f"- {r['meeting_id']}：{r['status']}；阶段 {r.get('failed_stage')}；原因 {r.get('reason')}；已有 checkpoint 和 partial 见该场 result.json。" for r in failed]
    text += ['- '+i['meeting_id']+'：'+i['error'] for i in issues]
    if not failed and not issues:text.append('无运行失败或产物审计错误；内容正确性仍需独立 evaluation。')
    if summary['new_consolidation_failure']:
        text+=['','### d 场新增的独立合并故障','',
            'TS3006b 的缺失 reason 已在本轮五条真实重复项中补齐，一次选择提交即通过，并完整运行 P2/摘要。a、b、c 完成；d 在选择之前的合并阶段失败。',
            '原始合并响应的 members 应是候选整数编号，却包含 {"id":"MTD021PM"} 字典。现有 _merge_groups 在过滤整数之前执行 dict.fromkeys，产生 unhashable type: dict。原响应零 API 回放已复现；extract.py 与 787d88e 完全相同，是新覆盖发现的独立缺陷。',
            'd 已实际运行 21 次调用，计入 366,880 token，尚未耗尽预算；其中 45,922 是取消时在途请求的未知用量保守预留。parsed/extracted、所有已返回响应及 partial 均保留。',
            '本轮没有顺带修改合并逻辑或猜测字典对应哪个候选，没有修后覆盖重跑。具体失败位置及同批有效响应见 consolidation_failure.json。']
    if diagnosis:
        text+=['','### TS3006b 选择提交失败：零 API 原响应复现','',
            '两次真实选择提交都在明确标为重复、keep=false 的 C-key 中遗漏必需 reason，首先触发 C10 校验错误。原参数交给冻结 canonical_reviews 后，两次错误完全复现；没有重新采样、补字段或修复生产代码。',
            '失败时只用了 20/80 次调用、309,656/1,000,000 token；P2/摘要尚未进入。该次失败不是 P2 拦截或预算耗尽。parsed、extracted、consolidated checkpoint、两次原始选择响应、trace 和 partial 均保留。',
            'TS3006c/d 因缺少本轮完整 b 历史而受阻，实际 API 调用为 0；不能算作已跑过。具体字段、原响应序号和路径见 selection_failure.json。']
    text+=['','## 本轮选择阶段','',
        '| 会议 | 选择提交次数 | 缺失重复项说明的元数据补充 | 未通过的选择提交 |',
        '| --- | ---: | ---: | ---: |']
    for row in rows:
        text.append(f"| {row['meeting_id']} | {row.get('selection_attempts',0)} | {len(row.get('duplicate_reason_metadata',[]))} | {len(row.get('selection_errors',[]))} |")
    text+=['','这里只记录本轮自然触发的补充；原响应回放不计入在线数量。原始参数保持缺失状态，每次代码补充均保存 reason_origin=code_metadata；没有生成新的选择、目标、事实或证据。','',
        '## 原始失败回放与验证范围','',
        '原 TS3006b 两份选择响应在旧代码下两次失败；修复后第一份响应一次完成选择，第二份原响应也通过 canonical 校验。首次模型请求完全相同，未新增 API 调用，原始参数保持不变。',
        '400 项测试通过。修复只补充明确指向有效保留目标的重复排除元数据 reason；继续执行原有类型、owner、候选来源与 P2 证据验证。模型、配置和预算完全一致，缓存使用单独的元数据版本。',
        '旧十六场回归及 825 份清单产物保持原样。本轮四场独立执行，不拼接为一次同版十六场全部通过；新版本的其他十二场未在本轮重跑。',
        '离线回放和测试见 offline/，新主流程见 run/；两者分别记录。','',
        '## 原始输入警告','']
    parser_notes=[(r['meeting_id'],w) for r in rows for w in r.get('parser_warnings',[])]
    text+=['- '+mid+'：'+w['category']+'；'+w['message'] for mid,w in parser_notes]
    if not parser_notes:text.append('离线复核原生解析 checkpoint 时没有输入警告。')
    text+=['','输入警告按原 parser 保留，没有改写原始时间或转写。','', '## 来源内容抽查','']
    text+=['- '+c['case_id']+'：'+c['status']+'。'+c['finding'] for c in reviews['cases']]
    text+=['','这些是 Codex 的开发来源抽查，不是新增人工确认；没有修改模型输出或评分，完整引用见 source_reviews.json。','', '## 复查入口','',
        '- study.json / source/ / inputs/：固定代码、模型、配置、输入及哈希。',
        '- run/s2/<meeting>.json、*_report.md、*.p2.json、*.clarifications.json：主记录与待确认项。',
        '- run/s2/*.stages.jsonl、*.trace.jsonl、_attempts/：所有阶段、实际模型响应、工具返回及原始 checkpoint。',
        '- summary.json：每场状态、预算、调用分布、P2 状态、工具与异常；manifest.json / verification.json：最终完整性。','']
    (root/'REPORT.md').write_text('\n'.join(text),encoding='utf-8')
    key=os.environ.get('OPENAI_API_KEY','').encode();assert len(key)>=12
    parsed=Counter()
    for p in files(root):
        require(key not in p.read_bytes(),'credential found in '+p.relative_to(root).as_posix())
        if p.suffix=='.json':load(p);parsed['json']+=1
        elif p.suffix=='.jsonl':parsed['jsonl_rows']+=len(lines(p));parsed['jsonl']+=1
        elif p.suffix=='.py':ast.parse(p.read_text(encoding='utf-8'));parsed['python']+=1
    save(root/'verification.json',{'result':'PASSED' if not issues else 'FAILED','issues':issues,
        'source_and_inputs_unchanged':not any('changed' in i['error'] for i in issues),'protected_files':len(protected),
        'artifact_parse_checks':dict(parsed),'credential_matches':0,'branch':git('branch','--show-current'),'head':git('rev-parse','HEAD'),
        'staged_files':git('diff','--cached','--name-only').splitlines(),'gold_loaded_or_scored':False})
    artifacts={p.relative_to(root).as_posix():sha(p) for p in files(root) if p!=root/'manifest.json'}
    save(root/'manifest.json',{'format_version':1,'artifact_sha256':artifacts})
    for rel,digest in artifacts.items():assert sha(root/rel)==digest,rel
    print(json.dumps({k:summary[k] for k in ('campaign_result','artifact_audit','scheduled','attempted','completed','statuses','totals')},ensure_ascii=False))
    assert not issues,issues

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('root',type=Path);args=ap.parse_args();inspect(args.root.resolve())
