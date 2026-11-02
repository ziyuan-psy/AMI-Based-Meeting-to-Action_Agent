"""Offline cost/source audit and fixed-input live regression validation; never scores gold."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').exists())
BEFORE=REPO/'agent/results/smoke_repairs/repair-v5'


def load(p):
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def events(p):
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def save(p,value):
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def outcomes(root,mid):
    meta=load(root/'run/s2'/f'{mid}.run.json')
    return load(root/'run/s2'/f'{mid}.p2.json')['items'] + load(Path(meta['checkpoints']['verified_omitted']))['outcomes']


def stats(items):
    lookups=[];invalid=[];seen=set();repeated=[]
    for it in items:
        for step in it['history']:
            if step.get('outcome')=='lookup':
                row={'item_key':it['item_key'],**step}
                key=(it['item_key'],step['action'],json.dumps(step['args'],sort_keys=True))
                if key in seen:
                    repeated.append(row)
                seen.add(key);lookups.append(row)
            elif step.get('outcome')=='invalid_action':
                invalid.append({'item_key':it['item_key'],**step})
    return {'lookups':len(lookups),'exact_repeated_executed_lookups':len(repeated),
            'repeated_lookups':repeated,'invalid_actions':len(invalid),
            'invalid_details':invalid,'executed_lookups':lookups}


def before_audit():
    items=outcomes(BEFORE,'TS3006b');result=load(BEFORE/'run/TS3006b.result.json')
    es=events(BEFORE/'run/s2/TS3006b.stages.jsonl')
    start=next(e['t'] for e in es if e['event']=='started' and e['stage']=='run')
    triage=next(e['t'] for e in es if e['event']=='completed' and e['stage']=='triage')
    upstream_seconds=round(triage-start,3)
    data={'meeting_id':'TS3006b','whole_calls':result['llm calls'],'whole_seconds':result['seconds'],
        'upstream_wall_seconds':upstream_seconds,'upstream_wall_share':round(upstream_seconds/result['seconds'],4),
        'p2_and_tail_wall_seconds':round(result['seconds']-upstream_seconds,3),
        'stage_usage':result['stages'],'trace_statistics':stats(items),
        'transport_errors':[a for a in result['runtime_control']['attempts'] if a['outcome']!='completed']}
    save(ROOT/'before_cost_audit.json',data)
    print(json.dumps({k:v for k,v in data.items() if k not in ('trace_statistics','transport_errors')},ensure_ascii=False))


def after_audit(root):
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
    for rel,digest in study['runtime']['code']['source_sha256'].items():assert sha(REPO/rel)==digest,rel
    rows=[];all_outputs={};all_outcomes={}
    for mid in study['meetings']:
        result=load(root/'run'/f'{mid}.result.json');assert result['status']=='COMPLETED',result
        records=load(root/'run/s2'/f'{mid}.json')
        p2=load(root/'run/s2'/f'{mid}.p2.json');qs=load(root/'run/s2'/f'{mid}.clarifications.json')
        store=Store(mid,allow_prior=True,run_dir=root/'run/s2')
        errors=check(records,store)+validate_outputs(p2,qs,records,store)
        assert not errors,errors
        meta=load(root/'run/s2'/f'{mid}.run.json')
        assert meta['code']['source_sha256']==study['runtime']['code']['source_sha256']
        for stage in ('extracted','consolidated','selected','normalized','triaged'):
            assert load(Path(meta['checkpoints'][stage]))==load(root/f'{mid}.{stage}.json')
        report=(root/'run/s2'/f'{mid}_report.md').read_text(encoding='utf-8')
        assert all(q['question'] in report for q in qs['requests'])
        for key,path in [('output',root/'run/s2'/f'{mid}.json'),('report',root/'run/s2'/f'{mid}_report.md'),
                         ('p2',root/'run/s2'/f'{mid}.p2.json'),('clarifications',root/'run/s2'/f'{mid}.clarifications.json')]:
            assert sha(path)==result[key+'_sha256']
        control=result['runtime_control'];debit=study['upstream'][mid]
        assert control['attempted_calls']+debit['attempts']<=80
        assert control['charged_tokens']+debit['charged_tokens']<=1000000
        assert result['seconds']+debit['elapsed_seconds']<=1200
        assert control['max_inflight_seen']<=4 and control['limits']['retries']==2
        assert control['limits']['tokens']==1000000-debit['charged_tokens']
        assert control['limits']['calls']==80-debit['attempts']
        after=outcomes(root,mid);before=outcomes(BEFORE,mid)
        current_stats=stats(after);prior_stats=stats(before)
        assert current_stats['exact_repeated_executed_lookups']==0
        all_outputs[mid]=records;all_outcomes[mid]=after
        old=load(BEFORE/'run'/f'{mid}.result.json')
        rows.append({'meeting_id':mid,'status':result['status'],'before_new_stage_calls':sum(old['stages'][s]['calls'] for s in ('p2_check','agent','abstract') if s in old['stages']),
            'before_new_stage_tokens':sum(old['stages'][s]['in']+old['stages'][s]['out'] for s in ('p2_check','agent','abstract') if s in old['stages']),
            'before_p2_and_tail_seconds':round(old['seconds']-debit['elapsed_seconds'],3),
            'after_new_stage_calls':control['attempted_calls'],'captured_upstream':debit,
            'equivalent_whole_calls':control['attempted_calls']+debit['attempts'],
            'new_known_tokens':sum(a.get('input_tokens',0)+a.get('output_tokens',0) for a in control['attempts'] if a['usage_known']),
            'new_charged_tokens':control['charged_tokens'],'new_seconds':result['seconds'],
            'before_trace':prior_stats,'after_trace':current_stats,
            'recordable':sum(i['recordable'] for i in after),'published':sum(i['recordable'] for i in p2['items']),
            'validation_errors':errors})
    assert sha(root/'run/s2/TS3006a.json')==study['prior_history_sha256']
    slides=[r for r in all_outputs['IS1008a'] if r['type']=='actions' and 'slides' in r['text'].lower()]
    assert len(slides)==1 and slides[0]['deadline'] and slides[0]['deadline_basis']=='explicit',slides
    slide_check=next(i for i in all_outcomes['IS1008a'] if 'slides' in i['item_text'].lower())
    assert slide_check['checks']['deadline']['result']=='pass'
    assert not any(q['field']=='deadline' for q in slide_check['clarifications'])
    time_quote=next(q for q in slide_check['checks']['deadline']['quotes'] if 'conclusion' in q['quote'].lower())
    links=[r for r in all_outputs['TS3006b'] if r.get('related_items')]
    old_links=[r for r in load(BEFORE/'run/s2/TS3006b.json') if r.get('related_items')]
    natural={'result':'PASSED','scope':study['scope'],'meetings':rows,'slides':slides,
             'slides_deadline_check':slide_check['checks']['deadline'],
             'slides_time_evidence':time_quote,'published_links':links,
             'historical_link_comparison':{'before':len(old_links),'after':len(links),
                'case':next((i for i in all_outcomes['TS3006b'] if i['item_key']=='TS3006b#10'),None)},'gold_scored':False,
             'limitations':['Fixed upstream live P2 regression, not fresh all-stage execution.',
                            'One model run per version; no significance or F1 claim.',
                            'New link outputs do not inherit an earlier human sign-off.',
                            'The previously generated continues link was not reproduced in this run; the item remains pending.',
                            'Repeated executed lookups fell, but format corrections increased and no overall speed/cost improvement is established.']}
    probe_path=root/'extraction_probe/result.json'
    if probe_path.exists():
        probe=load(probe_path)
        natural['independent_extraction_probe']={'slides':[r for r in probe['candidates'] if 'slides' in r['text'].lower()],
            'calls':load(root/'extraction_probe/control.json')['attempted_calls'],
            'not_used_to_seed_main_output':True}
    save(root/'summary.json',natural)
    print(json.dumps({'result':'PASSED','slides_deadline':slides[0]['deadline'],
        'meetings':[{k:v for k,v in r.items() if k not in ('before_trace','after_trace')}
                    for r in rows], 'published_links':len(links)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['before','after'])
    parser.add_argument('root',type=Path,nargs='?');args=parser.parse_args()
    before_audit() if args.command=='before' else after_audit(args.root.resolve())
