"""Read-only checks of the one real development acceptance trial; never loads gold."""
from __future__ import annotations
import json,re,sys
from collections import Counter
from pathlib import Path
import campaign as c

from mta.agent import _ids_in
from mta.observation import sha256
from mta.pipeline import saved_run_complete
from mta.p2 import validate_outputs
from mta.store import Store
from mta.validate import check

def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]

def check_one(mid,study):
    root=c.ROOT/'run/s2';meta=c.load(root/(mid+'.run.json'))
    errors=[];warnings=[]
    def require(ok,reason):
        if not ok:errors.append(reason)
    require(meta['status']=='COMPLETED','native meeting did not complete')
    require(meta['model']==study['runtime']['model'],'model changed')
    require(meta['config']==study['runtime']['config'],'configuration changed')
    require(meta['quality_policy']==study['runtime']['quality_policy'],'effective policy changed')
    require(meta['code']['git_commit']==study['commit'],'recorded code commit changed')
    require(meta['code']['source_sha256']==study['runtime']['code']['source_sha256'],'recorded source hashes changed')
    require(not meta['code']['dirty_source_files'],'runtime source is dirty')
    require(meta['prompts']==study['runtime']['prompts'],'recorded prompts changed')
    require(meta['input']['sha256']==study['input_sha256']['inputs/transcripts/'+mid+'.txt'],'input changed')
    control=meta['runtime_control'];limits=control['limits']
    require(control['attempted_calls']<=limits['calls'],'call budget exceeded')
    require(control['charged_tokens']<=limits['tokens'],'token budget exceeded')
    require(control['max_inflight_seen']<=limits['inflight'],'concurrency exceeded')
    require(not control['reserved_tokens'],'active reservation remains')
    require(meta['elapsed_seconds']<=limits['meeting_seconds']+5,'meeting deadline exceeded')
    stages=jsonl(root/(mid+'.stages.jsonl'));trace=jsonl(root/(mid+'.trace.jsonl'))
    requests=jsonl(c.ROOT/'run'/(mid+'.requests.jsonl'))
    require(bool(stages) and stages[-1]['event']=='finished','missing terminal stage trace')
    require(bool(requests) and requests[-1]['event']=='finished','missing terminal request trace')
    require(not any(e.get('source')=='cache' for e in stages),'response cache reused')
    admitted=[e for e in stages if e.get('event')=='request_admitted']
    finished=[e for e in stages if e.get('event')=='request_finished']
    require(len(admitted)==len(finished)==control['attempted_calls'],'admission trace/count mismatch')
    starts={e['attempt']:e['t'] for e in admitted}
    require(all(e['t']-starts[e['attempt']]<=limits['request_seconds']+2 for e in finished),'request timeout exceeded')
    result={'meeting_id':mid,'status':meta['status'],'errors':errors,'warnings':warnings,
            'elapsed_seconds':meta['elapsed_seconds'],'usage':meta['usage'],
            'runtime_control':{k:v for k,v in control.items() if k!='attempts'},
            'stage_events':len(stages),'agent_events':len(trace),'request_events':len(requests)}
    if meta['status']!='COMPLETED':return result
    store=Store(mid,allow_prior=True,run_dir=root)
    record=c.load(root/(mid+'.json'));p2=c.load(root/(mid+'.p2.json'))
    clarifications=c.load(root/(mid+'.clarifications.json'))
    errors.extend(check(record,store));errors.extend(validate_outputs(p2,clarifications,record,store))
    require(saved_run_complete(root/(mid+'.json')),'native completion marker/hash invalid')
    require(not p2['enabled'] and not p2['items'],'candidate claims full P2 checks')
    require(not clarifications['requests'],'unexpected full-P2 clarifications')
    require(p2['run_id']==meta['run_id']==clarifications['run_id'],'sidecar/native run identity differs')
    for name in ('p2','clarifications'):
        require(meta['result'][name+'_sha256']==sha256(root/(mid+'.'+name+'.json')),name+' sidecar hash differs')
    report=(root/(mid+'_report.md')).read_text(encoding='utf-8')
    require('Draft candidate' in report,'report omits candidate assurance label')
    require(meta['result']['p2_enabled'] is False,'result claims full P2 enabled')
    triaged=c.load(meta['checkpoints']['triaged'])
    require(len([r for r in record if r['type']!='abstract'])==len(triaged),'candidate mapping changed')
    source_review=[];citation_checks=[];tool_counts=Counter();outcomes=Counter()
    links=[];reference_count=0
    for n,item in enumerate(triaged):
        key=f'{mid}#{n}';seen=set(item['evidence_da_ids'][:12]);seen_initial=sorted(seen)
        events=sorted([e for e in trace if e.get('item')==key],key=lambda e:e.get('step') or 0)
        require(sum(e.get('outcome') in ('ok','tool_error') for e in events)<=c.config.MAX_CALLS_PER_ITEM,key+': item tool budget exceeded')
        for e in events:
            name=e.get('action');outcomes[e.get('outcome','unknown')]+=1
            if name and name!='finish':tool_counts[name]+=1
            if e.get('outcome')=='ok':
                require('tool_result' in e and isinstance(e.get('delivered_result'),str),key+': missing actual delivered result')
                data=json.loads(e['delivered_result'])
                require(len(e['delivered_result'])<=6000,key+': unbounded delivered result')
                new_ids=_ids_in(name,data);seen|=new_ids
                for value in new_ids:
                    match=re.match(r'^([A-Za-z]+[0-9]+[a-d])\.',value or '')
                    if match:require(match[1] in store.visible_ids(),key+': out-of-scope delivered source')
            if e.get('outcome')=='finished':
                cited=set(e['args']['evidence_da_ids'])
                require(cited<=seen,key+': finish cites unread dialogue')
                require(all(store.da_exists(d.split('.')[0],d) for d in cited),key+': invalid dialogue citation')
                citation_checks.append({'item_key':key,'initial_delivered_da_ids':seen_initial,
                    'finish_da_ids':sorted(cited),'additional_observed_ids':sorted(seen-set(seen_initial)),
                    'all_cited_dialogue_delivered':cited<=seen})
        final=record[n]
        for ref in final['reference']:
            reference_count+=1
            if ref['source_type'] in ('dialogue','participant_metadata'):
                require(ref['locator'].split('.')[0] in store.visible_ids(),key+': out-of-scope final reference')
        for link in final.get('related_items',[]):
            require(link['id'].split('.')[0] in store.visible_ids('prior'),key+': out-of-scope related item')
            links.append({'item_key':key,'record_id':final['id'],'text':final['text'],**link})
        if 'RESTORED_SOURCE_REVIEW' in item['flags']:
            source_review.append({'item_key':key,'record':final,'original_evidence':item['evidence_da_ids'],
                'successful_finish':any(e.get('outcome')=='finished' for e in events),
                'tool_calls':[{'step':e['step'],'action':e['action'],'outcome':e['outcome']} for e in events],
                'dialogue':[next(row for row in store.read_dialogue(r['locator'].split('.')[0],r['locator'],1) if row['da_id']==r['locator'])
                    for r in final['reference'] if r['source_type']=='dialogue']})
    result.update(item_counts=dict(Counter(r['type'] for r in record)),flagged_items=sum(bool(i['flags']) for i in triaged),
        restored_items=len(source_review),agent_outcomes=dict(outcomes),tool_calls=dict(tool_counts),
        cross_meeting_links=links,reference_count=reference_count,citation_checks=citation_checks,
        restored_source_review=source_review,p2_enabled=p2['enabled'],clarification_requests=len(clarifications['requests']),
        stage_seconds=meta['result'].get('seconds'),agent_fallbacks=meta['result'].get('agent_fallbacks'))
    require(sum(outcomes[k] for k in ('ok','tool_error'))<=c.config.MAX_CALLS_PER_MEETING,'meeting tool budget exceeded')
    result['recoverable_errors']=[{k:e.get(k) for k in ('item','step','action','outcome','error')}
        for e in trace if e.get('outcome') in ('invalid_finish','tool_error','invalid_action','no_tool_call','gave_up','agent_error')]
    return result

def main():
    c.route();study=c.load(c.ROOT/'study.json');state=c.load(c.ROOT/'state.json')
    assert state['status']=='FINISHED','Wait for native run to finish before final audit.'
    errors=[]
    for rel,digest in study['source_sha256'].items():
        if sha256(c.REPO/rel)!=digest:errors.append('source changed: '+rel)
    for rel,digest in study['input_sha256'].items():
        if sha256(c.ROOT/rel)!=digest:errors.append('input changed: '+rel)
    protected=c.load(c.ROOT/'protected_before.json')
    for rel,digest in protected.items():
        if sha256(c.REPO/rel)!=digest:errors.append('protected file changed: '+rel)
    preserved=0
    for rel,data in c.load(c.ROOT/'preservation.json').items():
        if sha256(c.REPO/rel/'manifest.json')!=data['manifest_sha256']:errors.append('prior manifest changed: '+rel)
        for path,digest in data['artifact_sha256'].items():
            if sha256(c.REPO/rel/path)!=digest:errors.append('prior artifact changed: '+rel+'/'+path)
            preserved+=1
    require_complete=all(r['status']=='COMPLETED' for r in state['meetings'])
    if not require_complete:errors.append('Not all four meetings completed in the one-shot trial.')
    meetings=[check_one(r['meeting_id'],study) for r in state['meetings'] if r['status'] not in ('SCHEDULED','BLOCKED_BY_PRIOR_FAILURE')]
    audit={'status':'FAILED' if errors or any(m['errors'] for m in meetings) else 'AUTOMATED_CHECKS_PASSED_SOURCE_REVIEW_REQUIRED',
        'commit':study['commit'],'profile':study['runtime']['quality_policy'],'execution_kind':study['execution_kind'],
        'completed':sum(r['status']=='COMPLETED' for r in state['meetings']),'scheduled':4,'errors':errors,
        'source_frozen':not any(e.startswith('source changed') for e in errors),
        'protected_files_checked':len(protected),'preserved_prior_artifacts':preserved,
        'gold_read':False,'heldout_opened':False,'model_judge_calls':0,'meetings':meetings}
    c.save(c.ROOT/'audit.json',audit)
    print(json.dumps({'status':audit['status'],'completed':audit['completed'],
        'errors':errors+[e for m in meetings for e in m['errors']],
        'meetings':[{k:m.get(k) for k in ('meeting_id','status','elapsed_seconds','item_counts','restored_items','tool_calls','agent_outcomes')} for m in meetings]},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
