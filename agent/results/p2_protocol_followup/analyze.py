"""Validate fixed-input artifacts and separate controller evidence from model variation."""
import argparse
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
OLD=REPO/'agent/results/deadline_trace_audit/fixed-v5'

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def events(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def outcomes(root,mid):
    meta=load(root/'run/s2'/f'{mid}.run.json')
    return load(root/'run/s2'/f'{mid}.p2.json')['items']+load(Path(meta['checkpoints']['verified_omitted']))['outcomes']
def statistics(items):
    invalid=[{'item':it['item_key'],**e} for it in items for e in it['history'] if e.get('outcome')=='invalid_action']
    checked=[{'item':it['item_key'],**e} for it in items for e in it['history'] if e.get('outcome')=='checked']
    lookups=[{'item':it['item_key'],**e} for it in items for e in it['history'] if e.get('outcome')=='lookup']
    normalized=[e for e in checked if e.get('protocol_normalizations')]
    return {'invalid_actions':len(invalid),'schema_rejections':sum(e['error'].startswith('Correct these argument fields:') for e in invalid),
        'lookup_rejections':sum(not e['error'].startswith('Correct these argument fields:') for e in invalid),
        'lookups':len(lookups),'duplicate_executions':sum(bool(e.get('tool_result',{}).get('duplicate')) for e in lookups),
        'invalid_details':invalid,'normalizations':normalized,
        'termination_counts':dict(Counter(i['termination_reason'] for i in items))}
def first_request(path,text):
    for e in events(path):
        if e.get('event')!='model_request' or len(e.get('messages',[]))!=1:continue
        try:initial=json.loads(e['messages'][0]['content'])
        except (KeyError,TypeError,ValueError):continue
        if initial.get('item',{}).get('text')==text:
            payload={k:e.get(k) for k in ('system','messages','tools','tool_choice','max_tokens','effort')}
            return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    raise AssertionError('Initial case request missing')

def main(root):
    sys.path.insert(0,str(root/'source/agent/src'))
    from mta import config
    from mta.p2 import validate_outputs
    from mta.validate import check
    from mta.store import Store
    study=load(root/'study.json')
    for k,v in study['runtime']['config'].items():setattr(config,k,v)
    config.DATA=root/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    for rel,digest in study['artifact_sha256'].items():assert sha(root/rel)==digest,rel
    working_source_matches=all(sha(REPO/rel)==digest for rel,digest in study['runtime']['code']['source_sha256'].items())
    assert sha(root/'run/s2/TS3006a.json')==study['prior_history_sha256']
    rows=[];all_cases=[];slides=[]
    for mid in study['meetings']:
        old=load(OLD/'run'/f'{mid}.result.json');now=load(root/'run'/f'{mid}.result.json')
        assert now['status']=='COMPLETED',now
        store=Store(mid,allow_prior=True,run_dir=root/'run/s2')
        p2=load(root/'run/s2'/f'{mid}.p2.json');questions=load(root/'run/s2'/f'{mid}.clarifications.json')
        records=load(root/'run/s2'/f'{mid}.json')
        assert not (errors:=check(records,store)+validate_outputs(p2,questions,records,store)),errors
        report=(root/'run/s2'/f'{mid}_report.md').read_text(encoding='utf-8')
        assert all(q['question'] in report for q in questions['requests'])
        for name,path in [('output',root/'run/s2'/f'{mid}.json'),('report',root/'run/s2'/f'{mid}_report.md'),
                          ('p2',root/'run/s2'/f'{mid}.p2.json'),('clarifications',root/'run/s2'/f'{mid}.clarifications.json')]:
            assert sha(path)==now[name+'_sha256']
        meta=load(root/'run/s2'/f'{mid}.run.json')
        assert meta['code']['source_sha256']==study['runtime']['code']['source_sha256']
        for stage in ('extracted','consolidated','selected','normalized','triaged'):
            assert load(Path(meta['checkpoints'][stage]))==load(root/f'{mid}.{stage}.json')
        a=now['runtime_control'];debit=study['upstream'][mid]
        assert a['limits']['calls']==80-debit['attempts'] and a['limits']['tokens']==1000000-debit['charged_tokens']
        assert a['attempted_calls']+debit['attempts']<=80 and a['charged_tokens']+debit['charged_tokens']<=1000000
        assert now['seconds']+debit['elapsed_seconds']<=1200 and a['limits']['retries']==2 and a['max_inflight_seen']<=4
        after=outcomes(root,mid);prior=outcomes(OLD,mid)
        before_stats,after_stats=statistics(prior),statistics(after)
        assert after_stats['duplicate_executions']==0
        rows.append({'meeting_id':mid,'before_calls':old['runtime_control']['attempted_calls'],
            'after_calls':a['attempted_calls'],'before_tokens':old['runtime_control']['charged_tokens'],
            'after_tokens':a['charged_tokens'],'before_seconds':old['seconds'],'after_seconds':now['seconds'],
            'captured_upstream':debit,'whole_budget_equivalent_calls':a['attempted_calls']+debit['attempts'],
            'before':before_stats,'after':after_stats,'validation_errors':errors,
            'published_links':[r for r in records if r.get('related_items')]})
        if mid=='IS1008a':slides=[r for r in records if r['type']=='actions' and 'slides' in r['text'].lower()]
        if mid=='TS3006b':
            case=next(i for i in after if i['item_key']=='TS3006b#10')
            all_cases.append({'source':'primary_cohort','outcome':case,
                'calls':meta['p2_admission']['attempts_by_item']['10'],
                'published_in_primary_output':bool(case['record_id']),
                'initial_request_sha256':first_request(root/f'{mid}.requests.jsonl',case['item_text'])})
    assert len(slides)==1 and slides[0]['deadline']=='at the conclusion of our meeting' and slides[0]['deadline_basis']=='explicit',slides
    for n in range(1,study['case_repetitions']+1):
        path=root/f'case-repeat-{n}';case=load(path/'outcome.json');run=load(path/'run.json')
        control=run['control'];debit=study['upstream']['TS3006b']
        assert control['failure'] is None and control['attempted_calls']+debit['attempts']<=80
        assert control['charged_tokens']+debit['charged_tokens']<=1000000 and control['max_inflight_seen']<=4
        item=copy.deepcopy(case);item['record_id']='TS3006b.sys.s.1' if item['recordable'] else None
        qs=copy.deepcopy(item['clarifications'])
        for q in qs:q['record_id']=item['record_id']
        item['clarifications']=qs
        records=[{'id':item['record_id'],'text':item['item_text'],'type':item['item_type'],**item['fields']}] if item['recordable'] else []
        p2={'format_version':1,'run_id':f'case-repeat-{n}','meeting_id':'TS3006b','enabled':True,'items':[item]}
        questions={'format_version':1,'run_id':f'case-repeat-{n}','meeting_id':'TS3006b','requests':qs}
        store=Store('TS3006b',allow_prior=True,run_dir=root/'run/s2')
        assert not (errors:=check(records,store)+validate_outputs(p2,questions,records,store)),errors
        all_cases.append({'source':f'case-repeat-{n}','outcome':case,'calls':control['attempted_calls'],
            'initial_request_sha256':first_request(path/'requests.jsonl',case['item_text']),'validation_errors':errors})
    signatures={r['initial_request_sha256'] for r in all_cases}
    assert len(signatures)==1,'Single-case repetitions do not have the same first model request'
    save(root/'summary.json',{'result':'PASSED','scope':study['scope'],'meetings':rows,'slides':slides,
        'working_source_matches':working_source_matches,
        'case_runs':all_cases,'same_initial_request':True,'case_linked_runs':sum(bool(r['outcome']['fields']['related_items']) for r in all_cases),
        'case_run_count':len(all_cases),'gold_scored':False,
        'limits':'Two single-case repetitions are diagnostic, not repeated full-meeting evaluation. Three observations do not establish a success rate or stability guarantee.'})
    print(json.dumps({'result':'PASSED','meetings':[{k:v for k,v in r.items() if k not in ('before','after','published_links')} for r in rows],
        'case_runs':[{'source':r['source'],'state':r['outcome']['state'],'links':r['outcome']['fields']['related_items'],
            'termination_reason':r['outcome']['termination_reason']} for r in all_cases]},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);args=parser.parse_args();main(args.root.resolve())
