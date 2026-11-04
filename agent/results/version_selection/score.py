"""Unchanged text-keyed matcher, original votes reused, no generation feedback."""
from __future__ import annotations
import json,time,copy
from concurrent.futures import ThreadPoolExecutor,as_completed
from study import REPO,ROOT,OLD,ISSUE,MEETINGS,load,save,sha,utc,verify
from mta import config
from mta.evaluate import make_llm_matcher,TYPES,prf,MATCH_SYSTEM,MATCH_SCHEMA
from mta.regression import score_items,RecordingLLM
from mta.judge import JsonCache
from mta.trace import StageTrace
from mta.control import RunControl,RunStopped
ARMS=('baseline','retention_actions','retention_actions_issues','selection213','selection_issue','p2_current')
class Offline:
    def forced_tool(self,*args,**kwargs):raise AssertionError('Missing cached vote in offline replay')
def old_target(mid):return OLD/load(OLD/'scoring_locations.json')[mid]
def votes(mid):
    a=load(old_target(mid)/'align_votes.json');b=load(ISSUE/'scoring'/mid/'align_votes.json')
    assert all(a[k]==b[k] for k in a.keys()&b.keys())
    return {**a,**b}
def summarize(rows):
    eligible=[r for r in rows if r['status']=='COMPLETED'];aggregate={}
    for arm in ARMS:
        aggregate[arm]={}
        for typ in TYPES:
            vals=[r['arms'][arm]['metrics'][typ] for r in eligible if 'unscored' not in r['arms'][arm]['metrics'][typ]]
            aggregate[arm][typ]={**prf(*(sum(v[k] for v in vals) for k in ('tp','sys','gold'))),'scored_meetings':len(vals)}
        aggregate[arm]['pooled']=prf(*(sum(aggregate[arm][typ][k] for typ in TYPES) for k in ('tp','sys','gold')))
    save(ROOT/'comparison.json',{'eligible_meetings':[r['meeting_id'] for r in eligible],
         'full16':len(eligible)==16,'arms':list(ARMS),'aggregate':aggregate,'per_meeting':rows,
         'new_scoring_calls':sum(r.get('runtime_control',{}).get('attempted_calls',0) for r in rows),
         'new_scoring_tokens':sum(r.get('runtime_control',{}).get('charged_tokens',0) for r in rows)})
def main():
    verify();assert not (ROOT/'scoring_state.json').exists();protocol=load(OLD/'scoring_protocol.json')
    assert config.EVAL_VOTES==protocol['votes']==3 and protocol['judge']==load(ROOT/'study.json')['runtime']['model']
    assert protocol['match_system']==MATCH_SYSTEM and protocol['match_schema']==MATCH_SCHEMA
    for rel,digest in protocol['scoring_source_sha256'].items():assert sha(REPO/rel)==digest
    for mid in MEETINGS:
        old=old_target(mid);inputs=load(old/'inputs.json')
        assert score_items(inputs['records']['baseline'],inputs['gold'],make_llm_matcher(Offline(),cache=votes(mid),votes=3),protocol['fewshot_excluded_ids'])==load(old/'result.json')['arms']['baseline']
    save(ROOT/'scoring_protocol.json',{**protocol,'arms':list(ARMS),'original16_baseline_replayed_api_calls':0,
         'driver_sha256':sha(__file__),'boundary_driver_sha256':sha(ROOT.parent/'boundaries.py'),
         'policy':'Single fixed boundary response per arm; original text-keyed votes reused; no resampling. Generation never sees gold, votes or scores.'})
    state={'status':'RUNNING','meetings':[]};rows=[];save(ROOT/'scoring_state.json',state)
    for mid in MEETINGS:
        folder=ROOT/'boundaries'/mid
        while True:
            paths=[folder/p/'result.json' for p in ('committed213','issue_current','p2_current')]
            if all(p.exists() and load(p)['status']!='RUNNING' for p in paths):break
            s=load(ROOT/'boundary_state.json');assert s['status']!='FINISHED','Missing terminal boundary results'
            time.sleep(1)
        verify();target=ROOT/'scoring'/mid;target.mkdir(parents=True)
        generated=[load(p) for p in paths]
        if any(r['status']!='COMPLETED' for r in generated):
            value={'meeting_id':mid,'status':'UNSCORED','boundary_statuses':[r['status'] for r in generated]}
            save(target/'result.json',value);rows.append(value);summarize(rows);continue
        records=load(folder/'replay.json')['records']
        records.update({a:r['record'] for a,r in zip(('selection213','selection_issue','p2_current'),generated)})
        gold=load(ROOT/'scoring_inputs/gold'/(mid+'.json'));assert sha(ROOT/'scoring_inputs/gold'/(mid+'.json'))==protocol['gold_sha256'][mid]
        save(target/'inputs.json',{'records':records,'gold':gold,'boundary_result_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths}})
        cache=JsonCache.get(target/'align_votes.json');cache.update_from(votes(mid))
        trace=StageTrace(target/'requests.jsonl');control=RunControl(trace=trace);client=RecordingLLM(trace)
        value={'meeting_id':mid,'status':'RUNNING','arms':{}};save(target/'result.json',value)
        try:
            matcher=make_llm_matcher(client.scoped(control),cache=cache,votes=3)
            # Sequential arms guarantee identical-text cache reuse even for newly judged texts.
            for arm in ARMS:
                value['arms'][arm]=score_items(records[arm],gold,matcher,protocol['fewshot_excluded_ids']);save(target/'result.json',value)
            assert value['arms']['baseline']==load(old_target(mid)/'result.json')['arms']['baseline']
            value['status']='COMPLETED'
        except RunStopped as exc:value.update(status=exc.failure['status'],failure=exc.failure)
        except Exception as exc:value.update(status='FAILED',error_type=type(exc).__name__,detail=str(exc))
        finally:
            control.seal();value['runtime_control']=control.snapshot();trace.finish(stage='scoring',event='finished',status=value['status']);save(target/'result.json',value)
        rows.append(value);state['meetings'].append({'meeting_id':mid,'status':value['status']});save(ROOT/'scoring_state.json',state);summarize(rows)
        print(json.dumps({'meeting_id':mid,'status':value['status'],'calls':control.snapshot()['attempted_calls']}),flush=True)
    state['status']='FINISHED';save(ROOT/'scoring_state.json',state);summarize(rows)
if __name__=='__main__':main()
