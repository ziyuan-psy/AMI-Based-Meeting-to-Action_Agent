"""Score available fixed predictions; unavailable generations are never assigned zero or rerun."""
from __future__ import annotations
import copy,json,time,shutil
from types import SimpleNamespace
from study import REPO,ROOT,MEETINGS,load,save,sha,utc,verify
from score import ARMS,votes,old_target
from mta import config
from mta.evaluate import make_llm_matcher,TYPES,prf
from mta.regression import score_items,RecordingLLM
from mta.trace import StageTrace
from mta.control import RunControl,RunStopped,stage_pool
from mta.judge import JsonCache

def aggregate(rows,arm):
    out={};eligible=[r for r in rows if arm in r['arms']]
    for typ in TYPES:
        vals=[r['arms'][arm]['metrics'][typ] for r in eligible if 'unscored' not in r['arms'][arm]['metrics'][typ]]
        out[typ]={**prf(*(sum(v[k] for v in vals) for k in ('tp','sys','gold'))),'scored_meetings':len(vals)}
    out['pooled']=prf(*(sum(out[t][k] for t in TYPES) for k in ('tp','sys','gold')))
    return out

def summary(rows):
    summaries={a:aggregate(rows,a) for a in ARMS};paired={}
    for arm in ARMS:
        eligible=[r for r in rows if arm in r['arms']]
        paired[arm]={'meetings':[r['meeting_id'] for r in eligible], 'arm':summaries[arm],
                     'baseline_on_same_meetings':aggregate(eligible,'baseline')}
    save(ROOT/'comparison.json',{'full16':len(rows)==16 and all(all(a in r['arms'] for a in ARMS) for r in rows),
        'meetings_processed':[r['meeting_id'] for r in rows],'arms':list(ARMS),'aggregate':summaries,'paired':paired,
        'per_arm_coverage':{a:len(paired[a]['meetings']) for a in ARMS},'per_meeting':rows,
        'new_scoring_calls':sum(r['runtime_control']['attempted_calls'] for r in rows),
        'new_scoring_tokens':sum(r['runtime_control']['charged_tokens'] for r in rows),
        'missing_generations_not_resampled':True,'missing_scores_not_zero_imputed':True})

def previous(mid):return [ROOT/'scoring'/mid,ROOT/'scoring_parallel'/mid]

def merge_votes(mid):
    seed=copy.deepcopy(votes(mid))
    for directory in previous(mid):
        path=directory/'align_votes.json'
        if not path.exists():continue
        for key,value in load(path).items():
            old=seed.get(key,[]);assert old[:min(len(old),len(value))]==value[:min(len(old),len(value))]
            if len(value)>len(old):seed[key]=value
    return seed

def past_attempts(mid):
    attempts=[];started=[]
    for directory in previous(mid):
        path=directory/'requests.jsonl'
        if not path.exists():continue
        events=[json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]
        admitted={e['attempt']:e for e in events if e.get('event')=='request_admitted'
                  and not e.get('request_stage','').startswith('replayed')}
        finished={e['attempt']:e for e in events if e.get('event')=='request_finished'}
        attempts.extend({**e,**finished.get(n,{})} for n,e in sorted(admitted.items()))
        started.extend(e['t'] for e in admitted.values())
    return attempts,max(0,time.time()-min(started)) if started else 0

def main():
    verify();assert not (ROOT/'available_scoring.json').exists()
    protocol=load(ROOT/'scoring_protocol.json');originals={}
    for part in ('scoring','scoring_parallel'):
        for p in (ROOT/part).rglob('*'):
            if p.is_file():originals[p.relative_to(ROOT).as_posix()]=sha(p)
    for name in ('scoring_state','comparison','scoring_locations'):
        p=ROOT/(name+'.json')
        if p.exists():shutil.copyfile(p,ROOT/(name+'_before_available.json'))
    save(ROOT/'available_scoring.json',{'created_at':utc(),'driver_sha256':sha(__file__),
        'preserved_score_artifacts':originals,'policy':'No regeneration. Reuse every completed vote. Charge all prior actual attempts, including unknown usage, without duplicating accounting replays. Preserve budget and retry limits. Compare only paired eligible meetings for each arm.'})
    rows=[];locations={};state={'status':'RUNNING','meetings':[]};save(ROOT/'scoring_state.json',state)
    for mid in MEETINGS:
        original=ROOT/'scoring'/mid;p=original/'result.json'
        if p.exists() and load(p)['status']=='COMPLETED':
            value=load(p);locations[mid]=str(original.relative_to(ROOT));rows.append(value)
            state['meetings'].append({'meeting_id':mid,'status':'COMPLETED'});save(ROOT/'scoring_state.json',state);save(ROOT/'scoring_locations.json',locations);summary(rows);continue
        folder=ROOT/'boundaries'/mid
        while True:
            paths=[folder/a/'result.json' for a in ('committed213','issue_current','p2_current')]
            if all(p.exists() and load(p)['status']!='RUNNING' for p in paths):break
            assert load(ROOT/'boundary_state.json')['status']!='FINISHED'
            time.sleep(1)
        verify();target=ROOT/'scoring_available'/mid;target.mkdir(parents=True)
        records=load(folder/'replay.json')['records'];unavailable={}
        for arm,p in zip(('selection213','selection_issue','p2_current'),paths):
            prediction=load(p)
            if prediction['status']=='COMPLETED':records[arm]=prediction['record']
            else:unavailable[arm]={'status':prediction['status'],'failure':prediction.get('failure'),
                                  'artifact_sha256':sha(p)}
        gold=load(ROOT/'scoring_inputs/gold'/(mid+'.json'))
        save(target/'inputs.json',{'records':records,'gold':gold,
             'boundary_result_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},'unavailable_generations':unavailable})
        completed={}
        for directory in previous(mid):
            p=directory/'result.json'
            if not p.exists():continue
            prior=load(p);original_inputs=load(directory/'inputs.json')
            for arm,value in prior.get('arms',{}).items():
                assert original_inputs['records'][arm]==records[arm]
                if arm in completed:assert completed[arm]==value
                completed[arm]=value
        # Baseline score is the frozen, already replay-verified original result, even if judging is unavailable.
        frozen=load(old_target(mid)/'result.json')['arms']['baseline']
        if 'baseline' in completed:assert completed['baseline']==frozen
        completed['baseline']=frozen
        seed=merge_votes(mid);cache=JsonCache.get(target/'align_votes.json');cache.update_from(seed)
        trace=StageTrace(target/'requests.jsonl');control=RunControl(trace=trace);client=RecordingLLM(trace)
        attempts,elapsed=past_attempts(mid)
        value={'meeting_id':mid,'status':'RUNNING','arms':completed,'unavailable_generations':unavailable,
               'prior_actual_scoring_attempts':len(attempts),'prior_elapsed_seconds_debit':elapsed}
        save(target/'result.json',value)
        try:
            for a in attempts:
                n=control.reserve('replayed_previous_scoring',a['reserved_tokens'],a['input_bound'],a['output_cap'])
                usage=SimpleNamespace(input_tokens=a['input_tokens'],output_tokens=a['output_tokens']) if a.get('usage_known') else None
                control.settle(n,usage,error_type=a.get('error_type') or ('usage_unknown_after_interruption' if not a.get('usage_known') else None))
            control.deadline-=elapsed
            matcher=make_llm_matcher(client.scoped(control),cache=cache,votes=3)
            with stage_pool(config.WORKERS,control) as pool:
                jobs={a:pool.submit(score_items,records[a],gold,matcher,protocol['fewshot_excluded_ids']) for a in ARMS if a in records and a not in completed}
                for a,job in jobs.items():value['arms'][a]=control.wait(job);save(target/'result.json',value)
            value['status']='COMPLETED'
        except RunStopped as exc:value.update(status='SCORING_PARTIAL',failure=exc.failure)
        except Exception as exc:value.update(status='SCORING_PARTIAL',error_type=type(exc).__name__,detail=str(exc))
        finally:
            control.seal();value['runtime_control']=control.snapshot();trace.finish(stage='scoring',event='finished',status=value['status']);save(target/'result.json',value)
        assert all(cache.get_value(k)[:len(v)]==v for k,v in seed.items())
        rows.append(value);locations[mid]=str(target.relative_to(ROOT));save(ROOT/'scoring_locations.json',locations)
        state['meetings'].append({'meeting_id':mid,'status':value['status']});save(ROOT/'scoring_state.json',state);summary(rows)
        print(json.dumps({'meeting_id':mid,'status':value['status'],'scored_arms':list(value['arms']),'unavailable_generations':list(unavailable),'attempts_including_prior':control.snapshot()['attempted_calls']}),flush=True)
    for rel,digest in originals.items():assert sha(ROOT/rel)==digest
    state['status']='FINISHED';save(ROOT/'scoring_state.json',state);summary(rows)
if __name__=='__main__':main()
