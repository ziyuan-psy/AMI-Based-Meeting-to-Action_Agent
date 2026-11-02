"""Independent boundary experiments; generation never receives gold or judge caches."""
from __future__ import annotations
import copy,json,time,traceback
from types import SimpleNamespace
from study import REPO,ROOT,OLD,ISSUE,MEETINGS,load,save,sha,utc,verify,module,route
from mta import config
from mta.control import RunControl,RunStopped,stage_pool
from mta.regression import RecordingLLM
from mta.trace import StageTrace,Trace
from mta.store import Store
from mta.pipeline import make_record,default_fields,_warm_store
from mta.p2 import run_p2_agent,validate_outputs
from mta.p2_admission import P2Admission
from mta.agent import MeetingBudget
from mta.validate import check
from mta.item_selection import ranked_cap

def debit(control,pack):
    """Replay prior accounting, not API calls; preserve the original whole-meeting headroom."""
    for a in pack['upstream_attempts']:
        n=control.reserve('replayed_upstream_'+a['stage'],a['reserved_tokens'],a['input_bound'],a['output_cap'])
        usage=SimpleNamespace(input_tokens=a['input_tokens'],output_tokens=a['output_tokens']) if a['usage_known'] else None
        control.settle(n,usage,error_type=a.get('error_type'))
        assert control.attempts[n]['charged_tokens']==a['charged_tokens']
    control.deadline-=pack['upstream_seconds_debit']

def record(mid,items,store):
    items=copy.deepcopy(items)
    for item in items:item['fields']=default_fields(store,item)
    return make_record(mid,items,[])

def selection(mid,profile,pack,store):
    target=ROOT/'boundaries'/mid/profile;target.mkdir(parents=True)
    trace=StageTrace(target/'requests.jsonl',meeting_id=mid,arm=profile)
    control=RunControl(trace=trace);client=RecordingLLM(trace);t=time.monotonic()
    value={'meeting_id':mid,'profile':profile,'status':'RUNNING','input_sha256':sha(ROOT/'fixed_inputs'/(mid+'.json'))}
    save(target/'result.json',value)
    try:
        debit(control,pack);engine=module(profile,'item_selection')
        selected=engine.select_items(client.scoped(control),copy.deepcopy(pack['normalized']),pack['acts'],
                                    cache=target/'cache.json',trace=trace,defer_action_limit=True)
        normalized=module(profile,'extract').normalize(selected,store.participant_ids())
        save(target/'selected.json',selected);value.update(status='COMPLETED',record=record(mid,normalized,store))
    except RunStopped as exc:value.update(status=exc.failure['status'],failure=exc.failure)
    except Exception as exc:value.update(status='FAILED',error_type=type(exc).__name__,detail=str(exc));traceback.print_exc()
    finally:
        control.seal();snapshot=control.snapshot();new=snapshot['attempts'][len(pack['upstream_attempts']):]
        value.update(seconds=round(time.monotonic()-t,3),runtime_control=snapshot,
                     replayed_upstream_calls=len(pack['upstream_attempts']),new_api_attempts=len(new),
                     new_api_charged_tokens=sum(a['charged_tokens'] for a in new))
        trace.finish(stage='select',event='finished',status=value['status']);save(target/'result.json',value)
    return value

def p2(mid,pack,store):
    target=ROOT/'boundaries'/mid/'p2_current';target.mkdir(parents=True)
    trace=StageTrace(target/'requests.jsonl',meeting_id=mid,arm='p2_current');agent_trace=Trace(target/'agent.trace.jsonl')
    control=RunControl(trace=trace);client=RecordingLLM(trace);t=time.monotonic()
    value={'meeting_id':mid,'status':'RUNNING','input_sha256':sha(ROOT/'fixed_inputs'/(mid+'.json'))}
    save(target/'result.json',value)
    try:
        debit(control,pack);items=copy.deepcopy(pack['p2_items']);keys=list(range(len(items)))
        admission=P2Admission(control,keys,config.P2_RESERVED_COMPLETION_CALLS);budget=MeetingBudget()
        def one(n):
            try:return run_p2_agent(client.scoped(admission.view(n)),store,items[n],budget,agent_trace,mid+'#'+str(n),
                                    progress=lambda state:save(target/('item_'+str(n)+'.json'),state))
            finally:admission.done(n)
        with stage_pool(config.WORKERS,control) as pool:
            jobs={n:pool.submit(one,n) for n in keys}
            outcomes=[control.wait(jobs[n]) for n in keys]
        checked=[]
        for n,(item,o) in enumerate(zip(items,outcomes)):
            o.update(record_id=None,item_type=item['type'])
            if o['recordable']:checked.append({**item,'text':o['item_text'],'fields':o['fields'],'item_key':mid+'#'+str(n)})
        # The fixed baseline pool already obeys the same type limits. No candidate expansion or hidden cap.
        final=make_record(mid,checked,[]);mapped={i['item_key']:r['id'] for i,r in zip(checked,final)}
        requests=[]
        for o in outcomes:
            o['record_id']=mapped.get(o['item_key'])
            for request in o['clarifications']:request['record_id']=o['record_id'];requests.append(request)
        payload={'format_version':1,'run_id':'fixed-414-'+mid,'meeting_id':mid,'enabled':True,'items':outcomes}
        clarifications={'format_version':1,'run_id':payload['run_id'],'meeting_id':mid,'requests':requests}
        errors=check(final,store)+validate_outputs(payload,clarifications,final,store)
        save(target/'p2.json',payload);save(target/'clarifications.json',clarifications)
        value.update(status='COMPLETED' if not errors else 'INVALID',record=final,errors=errors,
                     admission=admission.snapshot(),lookups_used=budget.used,
                     accepted=sum(o['state']=='accepted' for o in outcomes),pending=sum(o['state']=='pending' for o in outcomes),
                     quarantined=sum(not o['recordable'] for o in outcomes))
    except RunStopped as exc:value.update(status=exc.failure['status'],failure=exc.failure)
    except Exception as exc:value.update(status='FAILED',error_type=type(exc).__name__,detail=str(exc));traceback.print_exc()
    finally:
        control.seal();snapshot=control.snapshot();new=snapshot['attempts'][len(pack['upstream_attempts']):]
        value.update(seconds=round(time.monotonic()-t,3),runtime_control=snapshot,
                     replayed_upstream_calls=len(pack['upstream_attempts']),new_api_attempts=len(new),
                     new_api_charged_tokens=sum(a['charged_tokens'] for a in new))
        trace.finish(stage='p2',event='finished',status=value['status']);agent_trace.close();save(target/'result.json',value)
    return value

def replay(mid,pack,store):
    """Identical sampled merged candidates: only restoration/ranking rules differ."""
    legacy=module('baseline414','extract');modern=module('issue_current','extract')
    cache=load(OLD/'baseline/_cache'/('consolidate_'+mid+'.json'));runs=cache['runs'][:config.CONSOLIDATE_SAMPLES]
    extracted=load(OLD/'baseline/_cache'/('extract_False_'+mid+'.json'))
    # Extraction cache stores its final union explicitly; use the original real candidate boundary.
    cands=copy.deepcopy(extracted['chunk_pass']['candidates'])+legacy.union_actions(copy.deepcopy(extracted['action_pass']['samples'][:config.ACTION_SAMPLES]))
    voted=legacy.vote_items(copy.deepcopy(runs)) if len(runs)>1 else copy.deepcopy(runs[0])
    reference=legacy.normalize(legacy.restore_actions(copy.deepcopy(voted),copy.deepcopy(cands)),store.participant_ids())
    assert [(i['type'],i['text']) for i in reference]==[(i['type'],i['text']) for i in pack['normalized']]
    trace=StageTrace();retained=modern.restore_actions(copy.deepcopy(voted),copy.deepcopy(cands),trace,apply_cap=False)
    action_only=ranked_cap(copy.deepcopy(retained),trace)
    expanded=modern.restore_problems(copy.deepcopy(retained),copy.deepcopy(cands),trace)
    both=ranked_cap(expanded,trace)
    value={'meeting_id':mid,'status':'COMPLETED','api_calls':0,
           'claim':'Deterministic retention/ranking replay on IDENTICAL 414 recorded sampled outputs; does not measure new prompts.',
           'records':{'baseline':pack['baseline_record'],
                      'retention_actions':record(mid,modern.normalize(action_only,store.participant_ids()),store),
                      'retention_actions_issues':record(mid,modern.normalize(both,store.participant_ids()),store)},
           'events':trace.events,'inputs':{'cache_sha256':sha(OLD/'baseline/_cache'/('consolidate_'+mid+'.json')),
                                          'extraction_sha256':sha(OLD/'baseline/_cache'/('extract_False_'+mid+'.json'))}}
    save(ROOT/'boundaries'/mid/'replay.json',value);return value

def main():
    verify();route(config);assert not (ROOT/'boundary_state.json').exists()
    state={'status':'RUNNING','started_at':utc(),'meetings':[]};save(ROOT/'boundary_state.json',state)
    for mid in MEETINGS:
        verify();pack=load(ROOT/'fixed_inputs'/(mid+'.json'));store=Store(mid,True,ROOT/'frozen_history414');_warm_store(store)
        state['current_meeting']=mid;save(ROOT/'boundary_state.json',state)
        replay(mid,pack,store)
        for profile in ('committed213','issue_current'):
            r=selection(mid,profile,pack,store);print(json.dumps({'meeting_id':mid,'arm':profile,'status':r['status'],'new_api_attempts':r['new_api_attempts']}),flush=True)
        r=p2(mid,pack,store);print(json.dumps({'meeting_id':mid,'arm':'p2_current','status':r['status'],'new_api_attempts':r['new_api_attempts']}),flush=True)
        state['meetings'].append({'meeting_id':mid,'status':'FINISHED'});save(ROOT/'boundary_state.json',state)
    state.update(status='FINISHED',finished_at=utc());state.pop('current_meeting',None);save(ROOT/'boundary_state.json',state)

if __name__=='__main__':main()
