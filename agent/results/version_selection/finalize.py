"""Reproduce boundary scores offline and audit preserved files before reporting decisions."""
from __future__ import annotations
import os,json,collections
from pathlib import Path
from study import REPO,ROOT,OLD,ISSUE,MEETINGS,load,save,sha,utc,verify
from score import ARMS,Offline,old_target
from mta.evaluate import make_llm_matcher
from mta.regression import score_items

def audit():
    identity=verify();study=load(ROOT/'study.json');protocol=load(ROOT/'scoring_protocol.json')
    assert load(ROOT/'boundary_state.json')['status']=='FINISHED'
    assert load(ROOT/'scoring_state.json')['status']=='FINISHED'
    comparison=load(ROOT/'comparison.json');assert comparison['meetings_processed']==MEETINGS
    stable=load(ROOT/'stable_retention/comparison.json');assert stable['meetings']==MEETINGS
    assert sha(ROOT.parent/'stable_retention.py')==load(ROOT/'stable_retention/protocol.json')['driver_sha256']
    assert sha(ROOT.parent/'score.py')==protocol['driver_sha256']
    assert sha(ROOT.parent/'boundaries.py')==protocol['boundary_driver_sha256']
    all_results=[];replayed=0;generation_failures=[];scoring_partial=[]
    for mid in MEETINGS:
        target=ROOT/load(ROOT/'scoring_locations.json')[mid];result=load(target/'result.json');inputs=load(target/'inputs.json')
        matcher=make_llm_matcher(Offline(),cache=load(target/'align_votes.json'),votes=3)
        for arm in result['arms']:
            assert score_items(inputs['records'][arm],inputs['gold'],matcher,protocol['fewshot_excluded_ids'])==result['arms'][arm]
            replayed+=1
        alternate=ROOT/'stable_retention'/mid
        alt_inputs=load(alternate/'inputs.json');alt_result=load(alternate/'result.json')
        alt_matcher=make_llm_matcher(Offline(),cache=load(alternate/'align_votes.json'),votes=3)
        assert score_items(alt_inputs['record'],alt_inputs['gold'],alt_matcher,protocol['fewshot_excluded_ids'])==alt_result['score']
        replayed+=1
        assert result['arms']['baseline']==load(old_target(mid)/'result.json')['arms']['baseline']
        if result['status']!='COMPLETED':scoring_partial.append({'meeting_id':mid,'failure':result.get('failure'),'unscored_arms':[a for a in ARMS if a in inputs['records'] and a not in result['arms']]})
        for name,digest in inputs['boundary_result_sha256'].items():assert sha(ROOT/name)==digest
        pack=load(ROOT/'fixed_inputs'/(mid+'.json'))
        for previous,digest in pack['history_sha256'].items():
            assert previous[:-1]==mid[:-1] and previous[-1]<mid[-1]
            assert sha(ROOT/'frozen_history414'/(previous+'.json'))==digest
        for profile in ('committed213','issue_current','p2_current'):
            r=load(ROOT/'boundaries'/mid/profile/'result.json');assert r['status']!='RUNNING'
            if r['status']=='COMPLETED':assert not r.get('errors')
            else:generation_failures.append({'meeting_id':mid,'arm':profile,'status':r['status'],'failure':r.get('failure')})
            c=r['runtime_control'];limits=c['limits']
            assert c['attempted_calls']<=limits['calls']==80 and c['charged_tokens']<=limits['tokens']==1000000
            assert limits['inflight']==4 and limits['request_seconds']==300 and limits['meeting_seconds']==1200 and limits['retries']==2
            if r['status']=='COMPLETED':assert not c['failure'] and r['seconds']+pack['upstream_seconds_debit']<limits['meeting_seconds']
            assert r['new_api_attempts']==c['attempted_calls']-len(pack['upstream_attempts'])
            assert r['input_sha256']==sha(ROOT/'fixed_inputs'/(mid+'.json'))
            all_results.append(r)
    preserved=0
    for name,proof in load(ROOT/'preservation.json')['cohorts'].items():
        directory=REPO/name;assert sha(directory/'manifest.json')==proof['manifest_sha256']
        for rel,digest in proof['artifacts'].items():assert sha(directory/rel)==digest;preserved+=1
    protected=load(ROOT/'protected_before.json')
    for name,digest in protected.items():assert sha(REPO/name)==digest
    for mid,digest in study['gold_sha256'].items():
        assert sha(ROOT/'scoring_inputs/gold'/(mid+'.json'))==digest
        assert sha(REPO/'revised_gold_output'/(mid+'.json'))==digest
    for name,digest in study['existing_uncommitted_sha256'].items():assert sha(REPO/name)==digest
    jsons=jsonls=secrets=0;secret=os.environ.get('OPENAI_API_KEY','')
    for path in ROOT.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:continue
        data=path.read_bytes()
        if secret and len(secret)>8 and secret.encode() in data:secrets+=1
        if path.suffix=='.json':json.loads(data.decode('utf-8'));jsons+=1
        if path.suffix=='.jsonl':
            for line in data.decode('utf-8').splitlines():
                if line.strip():json.loads(line)
            jsonls+=1
    assert secrets==0
    parallel=load(ROOT/'parallel_scoring.json');assert sha(ROOT.parent/'parallel_score.py')==parallel['driver_sha256']
    for rel,digest in parallel['original_scoring_sha256'].items():assert sha(ROOT/rel)==digest
    available=load(ROOT/'available_scoring.json');assert sha(ROOT.parent/'score_available.py')==available['driver_sha256']
    for rel,digest in available['preserved_score_artifacts'].items():assert sha(ROOT/rel)==digest
    evidence={'status':'PASSED','finished_at':utc(),'commit':study['commit'],'branch':'agent-development',
        'source_files_unchanged':len(study['source_at_start']),'existing_uncommitted_files_unchanged':len(study['existing_uncommitted_sha256']),
        'model_config_budget_unchanged':identity['config']==study['runtime']['config'] and identity['model']==study['runtime']['model'],
        'protected_files_unchanged':len(protected),'prior_artifacts_unchanged':preserved,'dev_gold_unchanged':16,
        'heldout_opened':False,'generation_received_gold':False,'json_parsed':jsons,'jsonl_parsed':jsonls,
        'secret_occurrences':secrets,'offline_score_views_replayed':replayed,'offline_replay_api_calls':0,
        'fixed_meetings':16,'boundary_trials_attempted':len(all_results),
        'boundary_trials_completed':sum(r['status']=='COMPLETED' for r in all_results),
        'generation_failures':generation_failures,'scoring_partial':scoring_partial,
        'score_coverage':comparison['per_arm_coverage'],'source_restore_applied':False,
        'actual_new_generation_calls':sum(r['new_api_attempts'] for r in all_results),
        'actual_new_generation_tokens':sum(r['new_api_charged_tokens'] for r in all_results),
        'actual_new_scoring_calls':comparison['new_scoring_calls']+stable['scoring_api_calls'],
        'actual_new_scoring_tokens':comparison['new_scoring_tokens']+stable['scoring_charged_tokens'],
        'replayed_upstream_not_counted_as_new_api':True}
    save(ROOT/'verification.json',evidence)
    print(json.dumps(evidence,ensure_ascii=False))
    return evidence

def manifest():
    artifacts={p.relative_to(ROOT).as_posix():sha(p) for p in ROOT.rglob('*')
               if p.is_file() and p.name!='manifest.json' and '__pycache__' not in p.parts}
    save(ROOT/'manifest.json',{'created_at':utc(),'artifact_sha256':artifacts,
                             'scope':'Fixed-boundary development version selection; not final heldout evaluation or a cold complete pipeline.'})
    print(json.dumps({'manifest_artifacts':len(artifacts)}))

if __name__=='__main__':audit();manifest()
