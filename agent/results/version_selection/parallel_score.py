"""Resume preserved scoring with the unchanged four-worker shared controller."""
from __future__ import annotations
import json,time,copy,shutil
from pathlib import Path
from types import SimpleNamespace
from study import REPO,ROOT,OLD,MEETINGS,load,save,sha,utc,verify
from score import ARMS,Offline,old_target,votes,summarize
from mta import config
from mta.control import RunControl,RunStopped,stage_pool
from mta.trace import StageTrace
from mta.judge import JsonCache
from mta.regression import RecordingLLM,score_items
from mta.evaluate import make_llm_matcher

def carried(directory):
    """Account even an interrupted request whose usage was never delivered."""
    path=directory/'requests.jsonl'
    if not path.exists():return [],0
    events=[json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]
    admitted={e['attempt']:e for e in events if e.get('event')=='request_admitted'}
    finished={e['attempt']:e for e in events if e.get('event')=='request_finished'}
    attempts=[{**e,**finished.get(n,{})} for n,e in sorted(admitted.items())]
    return attempts,max(0,time.time()-min(e['t'] for e in admitted.values())) if admitted else 0

def main():
    verify();assert not (ROOT/'parallel_scoring.json').exists()
    protocol=load(ROOT/'scoring_protocol.json');assert config.WORKERS==4
    originals={}
    for p in (ROOT/'scoring').rglob('*'):
        if p.is_file():originals[p.relative_to(ROOT).as_posix()]=sha(p)
    for name in ('scoring_state.json','comparison.json'):
        shutil.copyfile(ROOT/name,ROOT/(Path(name).stem+'_before_parallel.json'))
    resume={'created_at':utc(),'driver_sha256':sha(__file__),'original_scoring_sha256':originals,
            'reason':'Use unchanged WORKERS=4 within the same per-meeting controller; preserve completed votes and charge interrupted unknown attempts fully.',
            'scoring_protocol_changed':False,'meetings':[]}
    save(ROOT/'parallel_scoring.json',resume);rows=[];locations={};state={'status':'RUNNING','meetings':[]}
    save(ROOT/'scoring_state.json',state)
    for mid in MEETINGS:
        original=ROOT/'scoring'/mid;prior=load(original/'result.json') if (original/'result.json').exists() else None
        if prior and prior['status']=='COMPLETED':
            value=prior;locations[mid]=str(original.relative_to(ROOT));rows.append(value)
            state['meetings'].append({'meeting_id':mid,'status':'COMPLETED'});save(ROOT/'scoring_state.json',state);save(ROOT/'scoring_locations.json',locations);continue
        folder=ROOT/'boundaries'/mid
        while True:
            paths=[folder/p/'result.json' for p in ('committed213','issue_current','p2_current')]
            if all(p.exists() and load(p)['status']!='RUNNING' for p in paths):break
            assert load(ROOT/'boundary_state.json')['status']!='FINISHED'
            time.sleep(1)
        verify();target=ROOT/'scoring_parallel'/mid;target.mkdir(parents=True)
        generated=[load(p) for p in paths];assert all(r['status']=='COMPLETED' for r in generated)
        records=load(folder/'replay.json')['records']
        records.update({a:r['record'] for a,r in zip(('selection213','selection_issue','p2_current'),generated)})
        gold=load(ROOT/'scoring_inputs/gold'/(mid+'.json'))
        inputs={'records':records,'gold':gold,'boundary_result_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths}}
        if prior:assert inputs==load(original/'inputs.json')
        save(target/'inputs.json',inputs)
        cache=JsonCache.get(target/'align_votes.json');seed=votes(mid)
        if prior:
            original_votes=load(original/'align_votes.json')
            assert all(original_votes[k]==v for k,v in seed.items())
            seed=original_votes
        cache.update_from(seed)
        trace=StageTrace(target/'requests.jsonl');control=RunControl(trace=trace);client=RecordingLLM(trace)
        attempts,elapsed=carried(original) if prior else ([],0)
        value={'meeting_id':mid,'status':'RUNNING','arms':copy.deepcopy(prior['arms']) if prior else {},
               'replayed_scoring_attempts':len(attempts),'replayed_elapsed_seconds':elapsed}
        save(target/'result.json',value)
        try:
            for a in attempts:
                n=control.reserve('replayed_interrupted_scoring',a['reserved_tokens'],a['input_bound'],a['output_cap'])
                usage=SimpleNamespace(input_tokens=a['input_tokens'],output_tokens=a['output_tokens']) if a.get('usage_known') else None
                control.settle(n,usage,error_type=a.get('error_type') or ('interrupted_usage_unknown' if not a.get('usage_known') else None))
            control.deadline-=elapsed
            matcher=make_llm_matcher(client.scoped(control),cache=cache,votes=3)
            with stage_pool(config.WORKERS,control) as pool:
                jobs={a:pool.submit(score_items,records[a],gold,matcher,protocol['fewshot_excluded_ids']) for a in ARMS if a not in value['arms']}
                for a,job in jobs.items():value['arms'][a]=control.wait(job);save(target/'result.json',value)
            assert value['arms']['baseline']==load(old_target(mid)/'result.json')['arms']['baseline']
            assert all(cache.get_value(k)[:len(v)]==v for k,v in seed.items())
            value['status']='COMPLETED'
        except RunStopped as exc:value.update(status=exc.failure['status'],failure=exc.failure)
        except Exception as exc:value.update(status='FAILED',error_type=type(exc).__name__,detail=str(exc))
        finally:
            control.seal();value['runtime_control']=control.snapshot();trace.finish(stage='scoring',event='finished',status=value['status']);save(target/'result.json',value)
        rows.append(value);locations[mid]=str(target.relative_to(ROOT));save(ROOT/'scoring_locations.json',locations)
        state['meetings'].append({'meeting_id':mid,'status':value['status']});save(ROOT/'scoring_state.json',state);summarize(rows)
        resume['meetings'].append({'meeting_id':mid,'status':value['status'],'replayed_attempts':len(attempts)});save(ROOT/'parallel_scoring.json',resume)
        print(json.dumps({'meeting_id':mid,'status':value['status'],'calls_including_carried':value['runtime_control']['attempted_calls']}),flush=True)
    for rel,digest in originals.items():assert sha(ROOT/rel)==digest
    state['status']='FINISHED';save(ROOT/'scoring_state.json',state);summarize(rows)
if __name__=='__main__':main()
