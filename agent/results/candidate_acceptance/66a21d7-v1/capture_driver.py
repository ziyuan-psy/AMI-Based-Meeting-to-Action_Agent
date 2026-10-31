"""One cold real TS3006 a->d acceptance trial; no gold, scoring or source changes."""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'agent/src/mta/pipeline.py').is_file())
ROOT=HERE/'66a21d7-v1'
FROZEN=REPO/'agent/results/recall_candidate/41654ab-v2'
VERSION=REPO/'agent/results/version_selection/213ccfc-v1'
MEETINGS=['TS3006'+c for c in 'abcd']
sys.path.insert(0,str(REPO/'agent/src'))
from mta import config,quality
from mta.llm import LLM
from mta.observation import write_json,sha256,runtime_identity,source_files
from mta.pipeline import run_meeting
from mta.regression import RecordingLLM
from mta.trace import StageTrace

def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path,value):write_json(Path(path),value)
def utc():return datetime.now(timezone.utc).isoformat()
def git(*args):return subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),*args],cwd=REPO).decode().strip()
def route():
    config.DATA=ROOT/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    config.QUALITY_PROFILE=quality.RECALL_CANDIDATE
def verify(client):
    s=load(ROOT/'study.json')
    assert git('branch','--show-current')=='agent-development' and git('rev-parse','HEAD')==s['commit']
    assert not git('diff','--cached','--name-only')
    for rel,digest in s['source_sha256'].items():assert sha256(REPO/rel)==digest,rel
    for rel,digest in s['input_sha256'].items():assert sha256(ROOT/rel)==digest,rel
    assert sha256(Path(__file__))==s['driver_sha256']
    identity=runtime_identity(client,'s2')
    assert identity['model']==s['runtime']['model'] and identity['config']==s['runtime']['config']
    assert identity['quality_policy']==s['runtime']['quality_policy']
    assert identity['quality_policy']['candidate'] and not identity['quality_policy']['p2_enabled']
    return identity

def prepare():
    assert not ROOT.exists();ROOT.mkdir(parents=True)
    assert git('branch','--show-current')=='agent-development' and git('rev-parse','--short','HEAD')=='66a21d7'
    assert not git('diff','--cached','--name-only')
    config.QUALITY_PROFILE=quality.RECALL_CANDIDATE
    previous=load(FROZEN/'study.json');client=LLM();identity=runtime_identity(client,'s2')
    assert identity['model']==previous['runtime']['model'] and identity['config']==previous['runtime']['config']
    assert identity['quality_policy']==previous['runtime']['quality_policy']
    source_hashes={}
    for path in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
        rel=path.relative_to(REPO).as_posix();assert sha256(path)==previous['source_sha256'][rel],rel
        target=ROOT/'source'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        source_hashes[rel]=sha256(path)
    inputs={}
    paths=[(config.TRANSCRIPTS/(mid+'.txt'),'inputs/transcripts/'+mid+'.txt') for mid in MEETINGS]
    paths += [(config.PARTICIPANTS,'inputs/participants.json'),(config.SCENARIO,'inputs/scenario_text.txt')]
    for source,rel in paths:
        assert sha256(source)==previous['input_sha256'][rel],rel
        target=ROOT/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        inputs[rel]=sha256(target)
    protected=load(FROZEN/'protected_before.json')
    assert all(sha256(REPO/p)==digest for p,digest in protected.items())
    save(ROOT/'protected_before.json',protected)
    preserve={}
    for old in (FROZEN,VERSION):
        data=load(old/'manifest.json')
        assert all(sha256(old/p)==digest for p,digest in data['artifact_sha256'].items())
        preserve[old.relative_to(REPO).as_posix()]={'manifest_sha256':sha256(old/'manifest.json'),'artifact_sha256':data['artifact_sha256']}
    save(ROOT/'preservation.json',preserve)
    (ROOT/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    shutil.copyfile(Path(__file__),ROOT/'capture_driver.py')
    save(ROOT/'study.json',{'created_at':utc(),'commit':git('rev-parse','HEAD'),'branch':'agent-development',
        'meetings':MEETINGS,'mode':'s2','runtime':identity,'source_sha256':source_hashes,'input_sha256':inputs,
        'driver_sha256':sha256(Path(__file__)),'execution_kind':'COLD_REAL_MODEL_NATIVE_S2_ACCEPTANCE',
        'cache_preexisting':False,'response_replay':False,'prior_records_seeded':False,
        'gold_read':False,'heldout_opened':False,'f1_tuning':False,
        'criteria':['four consecutive native completions','JSON/report/trace/sidecar validity',
                    'same configured model/resource limits','effective quality policy is explicit',
                    'only same-series earlier evidence','actual delivered citations','targeted task/citation source review'],
        'failure_policy':'Stop the series after any failed meeting. Preserve all attempts; no automatic regeneration or policy changes.'})
    save(ROOT/'state.json',{'status':'PREPARED','meetings':[{'meeting_id':mid,'status':'SCHEDULED'} for mid in MEETINGS]})
    route();verify(client)
    print(json.dumps({'prepared':True,'meetings':MEETINGS,'commit':'66a21d7','profile':config.QUALITY_PROFILE,'actual_api_calls':0}),flush=True)

def run():
    route();state=load(ROOT/'state.json');assert state['status']=='PREPARED'
    assert not (ROOT/'run').exists()
    runroot=ROOT/'run';runroot.mkdir();cache=runroot/'_cache'
    state.update(status='RUNNING',started_at=utc());save(ROOT/'state.json',state)
    stopped_by=None;t0=time.monotonic()
    for row in state['meetings']:
        mid=row['meeting_id']
        if stopped_by:
            row.update(status='BLOCKED_BY_PRIOR_FAILURE',blocked_by=stopped_by)
            save(ROOT/'state.json',state);continue
        requests=StageTrace(runroot/(mid+'.requests.jsonl'),meeting_id=mid)
        llm=RecordingLLM(requests);verify(llm)
        assert not (cache/config.QUALITY_PROFILE/('extract_False_'+mid+'.json')).exists()
        assert not (cache/config.QUALITY_PROFILE/('consolidate_'+mid+'.json')).exists()
        row.update(status='RUNNING',started_at=utc());state['current_meeting']=mid;save(ROOT/'state.json',state)
        print(json.dumps({'meeting_id':mid,'event':'started','cache':'cold'}),flush=True)
        result=run_meeting(config.TRANSCRIPTS/(mid+'.txt'),'s2',llm,runroot,cache)
        save(runroot/(mid+'.result.json'),result)
        requests.finish(stage='acceptance',event='finished',status=result['status'])
        row.update(status=result['status'],finished_at=utc(),result_path=(Path('run')/(mid+'.result.json')).as_posix())
        state['elapsed_seconds']=round(time.monotonic()-t0,3);save(ROOT/'state.json',state)
        c=result.get('runtime_control',{})
        print(json.dumps({'meeting_id':mid,'status':result['status'],'seconds':result.get('seconds'),
                          'attempts':c.get('attempted_calls'),'charged_tokens':c.get('charged_tokens'),
                          'reason':result.get('reason'),'failed_stage':result.get('failed_stage')}),flush=True)
        if result['status']!='COMPLETED':stopped_by=mid
    state.update(status='FINISHED',finished_at=utc(),elapsed_seconds=round(time.monotonic()-t0,3))
    state.pop('current_meeting',None);save(ROOT/'state.json',state)
    verify(LLM())
    print(json.dumps({'series_finished':True,'completed':sum(r['status']=='COMPLETED' for r in state['meetings']),'total':4}),flush=True)

def progress():
    s=load(ROOT/'state.json');mid=s.get('current_meeting')
    out={'status':s['status'],'completed':sum(r['status']=='COMPLETED' for r in s['meetings']),'current_meeting':mid}
    if mid:
        path=ROOT/'run/s2'/(mid+'.run.json')
        if path.exists():
            meta=load(path);out.update(stage=meta.get('current_stage'),checkpoints=len(meta.get('checkpoints',{})))
        path=ROOT/'run/s2'/(mid+'.stages.jsonl')
        if path.exists():
            events=[]
            for line in path.read_text(encoding='utf-8').splitlines():
                try:events.append(json.loads(line))
                except ValueError:pass
            out.update(attempts_admitted=sum(e.get('event')=='request_admitted' for e in events),
                       attempts_finished=sum(e.get('event')=='request_finished' for e in events))
    print(json.dumps(out),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','run','progress']);args=parser.parse_args()
    globals()[args.command]()
