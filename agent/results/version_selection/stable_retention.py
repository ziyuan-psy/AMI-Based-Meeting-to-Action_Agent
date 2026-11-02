"""Separate type-aware Action recovery from the newer ranking, on the same responses."""
from __future__ import annotations
import copy,json
from study import REPO,ROOT,OLD,MEETINGS,load,save,sha,utc,verify,module,route
from boundaries import record
from score import Offline,old_target,votes
from mta import config
from mta.store import Store
from mta.trace import StageTrace
from mta.control import RunControl
from mta.regression import RecordingLLM,score_items
from mta.evaluate import make_llm_matcher,TYPES,prf

def main():
    verify();route(config);targetroot=ROOT/'stable_retention';targetroot.mkdir(exist_ok=True);assert not (targetroot/'comparison.json').exists()
    legacy=module('baseline414','extract');modern=module('issue_current','extract');protocol=load(ROOT/'scoring_protocol.json')
    save(targetroot/'protocol.json',{'created_at':utc(),'driver_sha256':sha(__file__),
         'hypothesis':'Separate a previously combined choice: type-aware Action recovery versus new ranking. Keep the recorded vote outputs and legacy stable type cap; no new prompt/model samples.',
         'motivation':'IS1008b/c/d content replacement under new ranking versus TS3006a valid recovery. No upstream changes or resampling.',
         'scoring_protocol_sha256':sha(ROOT/'scoring_protocol.json'),
         'harness_correction':'Initial equality assertion included unmatched record metadata. Compare metrics/matches instead; content was identical and zero API calls were made for the interrupted ES2011c check. Original inputs, driver, protocol and trace preserved.'})
    rows=[]
    for mid in MEETINGS:
        completed=targetroot/mid/'result.json'
        if completed.exists() and load(completed)['status']=='COMPLETED':rows.append(load(completed));continue
        pack=load(ROOT/'fixed_inputs'/(mid+'.json'));store=Store(mid,True,ROOT/'frozen_history414')
        c=load(OLD/'baseline/_cache'/('extract_False_'+mid+'.json'))
        cands=copy.deepcopy(c['chunk_pass']['candidates'])+legacy.union_actions(copy.deepcopy(c['action_pass']['samples'][:config.ACTION_SAMPLES]))
        runs=load(OLD/'baseline/_cache'/('consolidate_'+mid+'.json'))['runs'][:config.CONSOLIDATE_SAMPLES]
        voted=legacy.vote_items(copy.deepcopy(runs)) if len(runs)>1 else copy.deepcopy(runs[0])
        trace=StageTrace();items=modern.restore_actions(copy.deepcopy(voted),cands,trace,apply_cap=False)
        capped=legacy._cap(items);normalized=modern.normalize(capped,store.participant_ids())
        records=record(mid,normalized,store);target=targetroot/mid;target.mkdir(exist_ok=True)
        base={(i['type'],i['text']) for i in pack['normalized']};current={(i['type'],i['text']) for i in records}
        new_inputs={'record':records,'gold':load(ROOT/'scoring_inputs/gold'/(mid+'.json')),
             'fixed_input_sha256':sha(ROOT/'fixed_inputs'/(mid+'.json')),'added':list(current-base),'removed':list(base-current),
             'restoration_events':trace.events,'cap_policy':'414 stable original order, unchanged 5/7/4'}
        resumed=(target/'inputs.json').exists()
        if resumed:
            old_inputs=load(target/'inputs.json');assert old_inputs['record']==records and old_inputs['gold']==new_inputs['gold']
            assert not any(json.loads(s).get('event')=='request_admitted' for s in (target/'requests.jsonl').read_text(encoding='utf-8').splitlines())
        else:save(target/'inputs.json',new_inputs)
        cache=votes(mid);request_trace=StageTrace(target/('requests_resume.jsonl' if resumed else 'requests.jsonl'));control=RunControl(trace=request_trace);client=RecordingLLM(request_trace)
        # Scoring-only cache is isolated and records exactly the votes actually received.
        from mta.judge import JsonCache
        disk=JsonCache.get(target/'align_votes.json');disk.update_from(cache)
        try:
            matcher=make_llm_matcher(client.scoped(control),cache=disk,votes=3)
            score=score_items(records,load(target/'inputs.json')['gold'],matcher,protocol['fewshot_excluded_ids'])
            if not (current-base or base-current):
                original=load(old_target(mid)/'result.json')['arms']['baseline']
                assert score['metrics']==original['metrics'] and score['matches']==original['matches']
            row={'meeting_id':mid,'status':'COMPLETED','score':score}
        finally:
            control.seal();request_trace.finish(stage='scoring',event='finished');
        row['runtime_control']=control.snapshot();save(target/'result.json',row);rows.append(row)
        print(json.dumps({'stable_retention':mid,'calls':control.snapshot()['attempted_calls']}),flush=True)
    aggregate={}
    for typ in TYPES:
        vals=[r['score']['metrics'][typ] for r in rows if 'unscored' not in r['score']['metrics'][typ]]
        aggregate[typ]={**prf(*(sum(v[k] for v in vals) for k in ('tp','sys','gold'))),'scored_meetings':len(vals)}
    aggregate['pooled']=prf(*(sum(aggregate[t][k] for t in TYPES) for k in ('tp','sys','gold')))
    save(targetroot/'comparison.json',{'meetings':MEETINGS,'aggregate':aggregate,'per_meeting':rows,
         'generation_api_calls':0,'scoring_api_calls':sum(r['runtime_control']['attempted_calls'] for r in rows),
         'scoring_charged_tokens':sum(r['runtime_control']['charged_tokens'] for r in rows)})
if __name__=='__main__':main()
