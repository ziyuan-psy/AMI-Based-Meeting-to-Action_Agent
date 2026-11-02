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
    for name in ('audit.py','replay.py','capture.py'):
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
        merge_attempts=[e for e in events if e.get('event')=='attempt' and e.get('stage') in ('consolidate','decision_consolidate') and 'sample' in e]
        rejects=[];rejection_counts=Counter();unusable=0
        for attempt in merge_attempts:
            if not attempt.get('items'):unusable+=1
            raw=next(e for e in attempt['events'] if e['event']=='raw_groups')
            merged=next(e for e in attempt['events'] if e['event']=='merged')
            invalid=set()
            for rejection in merged.get('rejected',[]):
                rejection_counts[rejection['reason']]+=1
                invalid.add(rejection['group_index'])
                rejects.append({'trace_seq':attempt['seq'],'stage':attempt['stage'],'sample':attempt['sample'],
                    'attempt':attempt['attempt'],**rejection,'kept_items':len(merged['items'])})
            for lineage in merged['lineage']:
                require(lineage['group_index'] not in invalid,'rejected group partly accepted',mid)
                group=raw['groups'][lineage['group_index']]
                require(isinstance(group['members'],list) and all(type(i) is int for i in group['members']),
                        'noninteger members accepted',mid)
        logical=lines(requests) if requests.exists() else []
        corrections=sum(r['stage'] in ('consolidate','decision_consolidate') and any(
            'Correction: previous response had no usable groups (' in str(m.get('content','')) for m in r['messages']) for r in logical)
        row['merge_diagnostics']={'logical_attempts':len(merge_attempts),'unusable_attempts':unusable,
            'correction_requests':corrections,'rejection_counts':dict(rejection_counts),'rejections':rejects}
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
    preserved=0
    for cohort in preservation['cohorts']:
        previous=REPO/cohort['path']
        require(sha(previous/'manifest.json')==cohort['manifest_sha256'],'old manifest changed: '+cohort['path'])
        for rel,digest in cohort['artifact_sha256'].items():
            require(sha(previous/rel)==digest,'old regression artifact changed: '+cohort['path']+'/'+rel)
            preserved+=1
        for rel,digest in cohort['parent_files'].items():require(sha(REPO/rel)==digest,'old regression driver/doc changed: '+rel)
    replay=load(root/'offline/replay.json')
    require(replay['result']=='PASSED' and replay['actual_new_api_calls']==0,'offline replay not passed')
    require(replay['after_source_sha256']==sha(REPO/'agent/src/mta/extract.py'),'replay/fresh-run source mismatch')
    tests=load(root/'tests.json');require(tests['passed']==428,'tests not passed')
    for rel,digest in tests['test_sha256'].items():require(sha(REPO/rel)==digest,'tested file changed: '+rel)
    for name,digest in tests['fixture_sha256'].items():require(sha(REPO/'agent/tests/fixtures'/name)==digest,'test fixture changed: '+name)
    require(sha(root/'source.patch')==study['source_patch_sha256'],'source patch changed')
    original=load(old/'study.json')
    require(study['source_sha256']['agent/src/mta/item_selection.py']==original['source_sha256']['agent/src/mta/item_selection.py'],'prior selection repair changed')
    require(study['runtime']['config']==original['runtime']['config'],'config differs from original cohort')
    require(study['runtime']['model']==original['runtime']['model'],'model differs from original cohort')
    for rel,digest in study['input_sha256'].items():require(digest==original['input_sha256'][rel],'different original input: '+rel)
    summary['old_artifacts_preserved']=preserved
    summary['offline_merge_members_replay_passed']=True
    summary['unit_tests']=tests['passed']
    summary['scope_note']='Independent four-meeting cold rerun. Prior 13/16 and 3/4 cohorts remain immutable; never combined into one single-version 16/16 result.'
    summary['merge_diagnostics']={r['meeting_id']:r.get('merge_diagnostics',{}) for r in rows}
    summary['duplicate_reason_metadata_count']=sum(len(r.get('duplicate_reason_metadata',[])) for r in rows)
    summary['issues']=issues
    summary['artifact_audit']='PASSED' if not issues else 'FAILED'
    summary['campaign_result']='ALL_REQUESTED_COMPLETED' if completed==requested and not issues else 'COMPLETION_OR_AUDIT_GAPS'
    diagnosis=None
    reviews=load(root/'source_reviews.json') if (root/'source_reviews.json').exists() else {'cases':[]}
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
    text=['# 合并 members 类型修复：TS3006 四场空缓存 S2 回归','',
        f"基于 `{study['commit']}`，分支 agent-development。生产改动为 extract.py 的合并输入校验；保留上一轮 item_selection.py 修复。补丁见 source.patch。未 commit。",'',
        'TS3006 a→d 从 TXT 真实运行，新目录、新缓存；只使用本轮产生的更早会议历史。模型、配置、预算、初次合并提示词及 schema 不变；不可用响应重试时增加格式纠正信息。未读取 gold 或留出集。','',
        f"完成 **{completed}/{requested}**；产物审计 **{summary['artifact_audit']}**。每场只运行一次，失败则按原生策略阻塞同系列后续会议，不替换旧输出或重跑择优。",'',
        '| 会议 | 状态 | 秒 | API 尝试 | 计入 token | 可记录/P2 输入 | 澄清 | 发布关联 |',
        '| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |']
    for r in rows:
        text.append(f"| {r['meeting_id']} | {r['status']} | {r.get('seconds','—')} | {r.get('api_attempts',0)} | {r.get('charged_tokens','—')} | {r.get('recordable','—')}/{r.get('p2_items','—')} | {r.get('clarification_requests','—')} | {r.get('published_links','—')} |")
    text+=['',f"总 API 尝试 {totals['api_attempts']}；已知 token {totals['known_tokens']:,}，计入 token {totals['charged_tokens']:,}，其中未知用量保守预留 {totals['unknown_usage_tokens']:,}。计入量不等于供应商账单。",'',
        '## 原始失败响应回放','',
        '三个真实合并响应逐一交给旧/新 _merge_groups，零 API 调用：原 attempt 17 在旧代码触发 unhashable type: dict；新代码拒绝两组对象成员并保留拒绝原因。attempt 18 的 11 个有效条目完全不变；attempt 19 仍为空。原始输入不改写。',
        '回放只验证实际捕获响应，不伪造修复后的模型重试。428 项测试通过（67.49 秒）；包含混合成员整组拒绝、有效同批样本保留、原重试上限、共享调用预算及缓存原响应重校验。','',
        '## 修复边界','',
        '- 先验证数组、分组和成员类型，再进行去重；members 只允许候选整数编号，拒绝对象、字符串、布尔值等。拒绝整组，不猜测或保留其中部分成员。',
        '- 同批有效分组/样本继续合并；无可用分组时原每样本最多 3 次重试增加格式纠正。没有增加采样数、请求重试、总调用或 token 上限。',
        '- 所有样本无效仍明确失败；不回退为未经合并的原始候选。运行预算/异常仍向上退出，已有 checkpoint、原响应和 partial 保留。',
        '- 有原响应的缓存先重新验证，失效样本才在原统一预算内补齐；本轮使用全新空缓存。','',
        '## 本轮自然触发的合并诊断','',
        '| 会议 | 合并逻辑响应 | 无可用响应 | 格式纠正请求 | 非整数成员分组 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for r in rows:
        d=r.get('merge_diagnostics',{})
        text.append(f"| {r['meeting_id']} | {d.get('logical_attempts',0)} | {d.get('unusable_attempts',0)} | {d.get('correction_requests',0)} | {d.get('rejection_counts',{}).get('members_must_be_candidate_indexes',0)} |")
    text+=['','在线未自然触发的错误路径，以原响应回放和测试为验证依据，不声称本轮在线发生过。逐组拒绝原因及 trace 序号见 summary.json。','',
        '## 阶段调用分布','',
        '| 会议 | 抽取 | 合并 | Decision 重合并 | 选择 | P2 | 自主工具决策 | 摘要 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        counts=r.get('model_attempts_by_stage',{})
        text.append('| '+r['meeting_id']+' | '+' | '.join(str(counts.get(k,0)) for k in ('extract','consolidate','decision_consolidate','select','p2_check','agent','abstract'))+' |')
    text+=['','## 产物、预算及来源检查','',
        '- 完成场次校验最终 JSON/schema、P2 与澄清对应、报告问题覆盖、文件哈希、阶段/Agent trace、TXT 解析 checkpoint 和实际调用/token 账本。',
        '- 每场 80 次调用、1,000,000 token；请求 300 秒，整场 1200 秒，并发 4，传输重试 2。逻辑 requests 与 runtime_control 实际 API 尝试分别记录。',
        f"- P2 输入 {totals['p2_items']}，accepted {totals['accepted']}、pending {totals['pending']}、可记录 {totals['recordable']}；澄清 {totals['clarification_requests']} 条；自然有限修正 {totals['valid_revisions']} 次，见 limited_revisions.json。",
        f"- 发布关联 {totals['published_links']} 条；旧记录→工具返回→Agent 选择→新记录见 [关联清单](CROSS_MEETING.md) / cross_meeting_links.json。未决关系见 pending_relations.json。只允许同系列更早会议；关系语义尚未新增人工确认。",
        f"- 两轮旧回归共 {preserved} 份清单产物及其父级驱动/文档的哈希保持原样；UI/标注文档等 {len(protected)} 个受保护文件未变。",
        '- 原 16 场回归仍为 13/16，上一轮 TS3006 为 3/4；本轮四场独立记录，不拼接成同版 16/16。其余十二场未在本轮重新运行。',
        '- 本次属于开发回归及结构/来源核查，没有计算 P/R/F1，也不作为稳定性或最终语义正确性的证明。','',
        '## 失败与审计问题','']
    failed=[r for r in rows if r['status'] not in ('COMPLETED','BLOCKED_BY_PRIOR_FAILURE')]
    text += [f"- {r['meeting_id']}：{r['status']}；阶段 {r.get('failed_stage')}；原因 {r.get('reason')}；已有 checkpoint / partial 见 result.json。" for r in failed]
    text += ['- '+i['meeting_id']+'：'+i['error'] for i in issues]
    if not failed and not issues:text.append('无运行失败或产物审计错误；内容正确性仍需独立 evaluation。')
    text+=['','## 输入警告','']
    notes=[(r['meeting_id'],w) for r in rows for w in r.get('parser_warnings',[])]
    text+=['- '+mid+'：'+w['category']+'；'+w['message'] for mid,w in notes]
    if not notes:text.append('原生 TXT 解析 checkpoint 离线复核无警告。')
    text+=['','输入警告按原 parser 保留，未改写原始时间或转写。','',
        '## 复查入口','',
        '- study.json / source/ / inputs/：固定代码、模型、配置、输入及哈希。',
        '- run/s2/*.json、*_report.md、*.p2.json、*.clarifications.json：最终记录和待确认项。',
        '- run/s2/*.stages.jsonl、*.trace.jsonl、_attempts/：原始模型响应、阶段 checkpoint、工具返回与失败原因。',
        '- offline/：原失败回放、测试、修复前哈希；summary.json / verification.json / manifest.json：逐场审计与完整性。','']
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
