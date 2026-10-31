"""Handoff runner: explicit fresh output/cache policy, frozen native pipeline, no gold reads.

--verify-only checks code/config/model without opening any held-out input or calling the API.
Prediction runs are the evaluator's action, after acceptance; scoring is a separate step.
"""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
import campaign as c
from mta import config,quality
from mta.llm import LLM
from mta.observation import runtime_identity,sha256
from mta.pipeline import run_meeting
from mta.regression import RecordingLLM
from mta.trace import StageTrace

def verify():
    study=c.load(c.ROOT/'study.json')
    # Evidence/docs-only descendant commits are allowed; runtime bytes must still match exactly.
    assert study['commit'] in c.git('rev-list','HEAD').splitlines(),'HEAD is not based on the accepted runtime.'
    assert not c.git('diff','--cached','--name-only'),'Index is not empty.'
    for path,digest in study['source_sha256'].items():
        assert sha256(c.REPO/path)==digest,'Runtime/schema changed: '+path
    config.QUALITY_PROFILE=quality.RECALL_CANDIDATE
    identity=runtime_identity(LLM(),'s2')  # constructor only; no model request
    assert identity['code']['source_sha256']==study['runtime']['code']['source_sha256'],'Runtime source file set/hashes differ.'
    assert identity['model']==study['runtime']['model'],'Model/endpoint differs from acceptance.'
    assert identity['config']==study['runtime']['config'],'Configuration/budgets differ from acceptance.'
    assert identity['quality_policy']==study['runtime']['quality_policy'],'Effective policy differs.'
    return identity

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--verify-only',action='store_true')
    ap.add_argument('--series',nargs='+',choices=['ES2004','TS3007','TS3006'])
    ap.add_argument('--modes',nargs='+',choices=['b0','s1','s2'],default=['b0','s1','s2'])
    ap.add_argument('--out',type=Path)
    ap.add_argument('--cache-policy',choices=['shared_s1_s2','cold_each_mode'],default='shared_s1_s2',
        help='shared_s1_s2 fixes extraction/merge inputs across the context ablation; S2 timing is incremental')
    ap.add_argument('--allow-heldout',action='store_true',help='Evaluator only: explicitly open frozen held-out inputs')
    args=ap.parse_args();identity=verify()
    if args.verify_only:
        print(json.dumps({'status':'FROZEN_CODE_CONFIG_MODEL_MATCH','commit':identity['code']['git_commit'],
                          'runtime_reference_commit':c.load(c.ROOT/'study.json')['commit'],
                          'model':identity['model'],'profile':identity['quality_policy']['name'],
                          'api_calls':0,'heldout_opened':False}));return 0
    if not args.series or not args.out:ap.error('--series and --out are required for prediction runs')
    if len(set(args.series))!=len(args.series) or len(set(args.modes))!=len(args.modes):ap.error('Do not repeat series/modes')
    if any(s in config.SC_SERIES for s in args.series) and not args.allow_heldout:ap.error('Held-out runs require --allow-heldout')
    acceptance=c.load(c.ROOT/'acceptance.json')
    assert acceptance['status']=='PASSED_WITH_DOCUMENTED_LIMITATIONS','Development acceptance is not complete.'
    modes=[m for m in ('b0','s1','s2') if m in args.modes]
    out=args.out.resolve();assert not out.exists(),'Choose a new output directory; preserve all earlier attempts.'
    out.mkdir(parents=True)
    state={'status':'RUNNING','started_at':c.utc(),'commit':identity['code']['git_commit'],
           'runtime_reference_commit':c.load(c.ROOT/'study.json')['commit'],
           'runtime':identity,'driver_sha256':sha256(Path(__file__)),
           'development_acceptance_sha256':sha256(c.ROOT/'acceptance.json'),
           'cache_policy':args.cache_policy,'modes':modes,'series':args.series,'gold_read':False,
           'heldout_opened':False,'stop_on_failure':True,'meetings':[]}
    c.save(out/'campaign.json',state);started=time.monotonic()
    for series in args.series:
        for mode in modes:
            for letter in 'abcd':
                verify();mid=series+letter;target=config.TRANSCRIPTS/(mid+'.txt')
                row={'meeting_id':mid,'mode':mode,'status':'RUNNING','started_at':c.utc()}
                state['meetings'].append(row);c.save(out/'campaign.json',state)
                cache_key='s1_s2' if mode in ('s1','s2') and args.cache_policy=='shared_s1_s2' else mode
                cache=out/'_cache'/series/cache_key
                cache_was_present=cache.exists();row['cache_was_present']=cache_was_present
                row['input_sha256']=sha256(target)
                state['heldout_opened']|=series in config.SC_SERIES
                requests=StageTrace(out/'requests'/f'{mid}.{mode}.jsonl',meeting_id=mid,mode=mode)
                result=run_meeting(target,mode,RecordingLLM(requests),out/series,cache)
                c.save(out/'results'/f'{mid}.{mode}.json',result)
                requests.finish(stage='evaluation_prediction',event='finished',status=result['status'])
                row.update(status=result['status'],finished_at=c.utc(),
                           result_path=f'results/{mid}.{mode}.json')
                c.save(out/'campaign.json',state)
                print(json.dumps({'meeting_id':mid,'mode':mode,'status':result['status'],
                                  'calls':result.get('runtime_control',{}).get('attempted_calls')}),flush=True)
                if result['status']!='COMPLETED':
                    state.update(status='STOPPED_AFTER_FAILURE',finished_at=c.utc(),
                                 elapsed_seconds=round(time.monotonic()-started,3))
                    c.save(out/'campaign.json',state);return 1
    verify();state.update(status='COMPLETED',finished_at=c.utc(),elapsed_seconds=round(time.monotonic()-started,3))
    c.save(out/'campaign.json',state)
    c.save(out/'manifest.json',{'artifact_sha256':{p.relative_to(out).as_posix():sha256(p)
        for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'}})
    return 0

if __name__=='__main__':sys.exit(main())
