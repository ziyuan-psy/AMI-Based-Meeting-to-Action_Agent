"""One cold S2 campaign on the 16 SB meetings. No gold, replay, tuning or extra retries."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path.insert(0,str(REPO/'agent/src'))
SERIES=('ES2011','IS1008','TS3004','TS3006')
MEETINGS=[s+c for s in SERIES for c in 'abcd']
COMMIT='787d88e34d803a3bcafa4817720634f2dd74b50e'

def utc():return datetime.now(timezone.utc).isoformat()
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    from mta.observation import write_json
    write_json(p,value)
def git(*args):
    return subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),*args],cwd=REPO).decode('utf-8').strip()
def event(root,kind,**values):
    row={'time':utc(),'event':kind,**values}
    with (root/'campaign.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps(row,ensure_ascii=False),flush=True)

def verify(root):
    from mta import config
    from mta.llm import LLM
    from mta.observation import runtime_identity
    study=load(root/'study.json')
    assert git('rev-parse','HEAD')==COMMIT and git('branch','--show-current')=='agent-development'
    for rel,digest in study['source_sha256'].items():assert sha(REPO/rel)==digest,rel
    for rel,digest in study['input_sha256'].items():assert sha(root/rel)==digest,rel
    assert sha(root/'driver.py')==study['driver_sha256']
    client=LLM();identity=runtime_identity(client)
    assert identity['config']==study['runtime']['config'],'Settings changed'
    assert identity['model']==study['runtime']['model'],'Model changed'
    assert not identity['code']['dirty_source_files']
    return client

def inputs(root):
    from mta import config
    config.DATA=root/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'

def prepare(root):
    from mta import config
    from mta.llm import LLM
    from mta.observation import runtime_identity,source_files
    assert not root.exists(),'Never overwrite an existing campaign'
    assert git('rev-parse','HEAD')==COMMIT and git('branch','--show-current')=='agent-development'
    assert not git('diff','--cached','--name-only')
    assert os.environ.get('OPENAI_API_KEY'),'API credential unavailable; value not printed'
    identity=runtime_identity(LLM())
    assert not identity['code']['dirty_source_files']
    expected=copy.deepcopy(load(REPO/'agent/results/p2_protocol_followup/fixed-v5/study.json')['runtime']['config'])
    expected['P2_PROTOCOL_VERSION']=19
    assert identity['config']==expected,'Do not alter the existing config/budgets'
    assert identity['model']['name']=='@30875-fall26-openai/gpt-5-nano'
    assert identity['model']['endpoint']=='https://api.portkey.ai/v1'
    root.mkdir(parents=True);(root/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    source={};eol=[]
    for p in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
        rel=p.relative_to(REPO).as_posix();blob=subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),'show',COMMIT+':'+rel],cwd=REPO)
        assert blob.replace(b'\r\n',b'\n')==p.read_bytes().replace(b'\r\n',b'\n'),rel
        if blob!=p.read_bytes():eol.append(rel)
        out=root/'source'/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,out);source[rel]=sha(p)
    copied={}
    for mid in MEETINGS:
        p=config.TRANSCRIPTS/(mid+'.txt');out=root/'inputs/transcripts'/p.name
        out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,out);copied[out.relative_to(root).as_posix()]=sha(out)
    for p in (config.PARTICIPANTS,config.SCENARIO):
        out=root/'inputs'/p.name;shutil.copyfile(p,out);copied[out.relative_to(root).as_posix()]=sha(out)
    shutil.copyfile(Path(__file__),root/'driver.py')
    previous=load(REPO/'agent/results/p2_protocol_followup/before.json')['protected_files']
    protected={rel:sha(REPO/rel) for rel in previous}
    assert protected==previous
    save(root/'protected_before.json',protected)
    save(root/'study.json',{'created_at':utc(),'commit':COMMIT,'branch':'agent-development','mode':'s2',
        'series':list(SERIES),'meetings':MEETINGS,'execution_kind':'COLD_NATIVE_PIPELINE_FULL_S2',
        'runtime':identity,'source_sha256':source,'source_eol_only_differences_from_commit':eol,
        'input_sha256':copied,'driver_sha256':sha(root/'driver.py'),
        'cache_preexisting':False,'run_directory_preexisting':False,'seeded_responses':False,
        'prior_records_seeded':False,'captured_upstream_budget_debit':False,'gold_loaded_or_scored':False,
        'heldout_transcripts_or_gold_opened':False,'extra_model_judging':False,
        'series_failure_policy':'Stop downstream meetings of a failed series, as native CLI; continue the next series. Never substitute historical outputs.',
        'scope':'One attempt per meeting; native transport retries share its unchanged budget. No semantic accuracy or stability claim from structural validation.'})
    save(root/'state.json',{'status':'PREPARED','meetings':[{'meeting_id':m,'status':'SCHEDULED'} for m in MEETINGS]})
    event(root,'prepared',meetings=len(MEETINGS),commit=COMMIT,actual_api_calls=0)

def run(root):
    from mta.llm import LLM
    from mta.pipeline import run_meeting
    state=load(root/'state.json');assert state['status']=='PREPARED','No rerun/resume in the primary campaign'
    assert not (root/'run').exists()
    verify(root);inputs(root)
    runroot=root/'run';cache=runroot/'_cache';runroot.mkdir()
    state.update(status='RUNNING',started_at=utc());save(root/'state.json',state)
    lock=threading.Lock()
    class Requests(LLM):
        def _create(self,system,messages,tools=None,tool_choice=None,max_tokens=4096,effort=None,stage=None,control=None):
            # Logical input only. Native control trace remains authoritative for attempts/retries/usage.
            entry={'time':utc(),'kind':'logical_request','meeting_id':self.meeting_id,'stage':stage,
                'system':system,'messages':messages,'tools':tools,'tool_choice':tool_choice,'max_tokens':max_tokens,'effort':effort}
            with lock:
                with (runroot/(self.meeting_id+'.requests.jsonl')).open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
            return super()._create(system,messages,tools,tool_choice,max_tokens,effort,stage,control)
    started=time.monotonic()
    for series in SERIES:
        llm=Requests();blocked_by=None
        for letter in 'abcd':
            mid=series+letter;row=next(r for r in state['meetings'] if r['meeting_id']==mid)
            if blocked_by:
                row.update(status='BLOCKED_BY_PRIOR_FAILURE',blocked_by=blocked_by,actual_api_calls=0)
                save(root/'state.json',state);event(root,'blocked',meeting_id=mid,blocked_by=blocked_by);continue
            verify(root);inputs(root);llm.meeting_id=mid
            assert not (cache/('extract_False_'+mid+'.json')).exists()
            assert not (runroot/'s2'/(mid+'.run.json')).exists()
            row.update(status='RUNNING',started_at=utc());state['current_meeting']=mid;save(root/'state.json',state)
            event(root,'meeting_started',meeting_id=mid)
            result=run_meeting(root/'inputs/transcripts'/(mid+'.txt'),'s2',llm,runroot,cache_dir=cache)
            save(runroot/(mid+'.result.json'),result)
            row.update(status=result['status'],finished_at=utc(),result_path=(Path('run')/(mid+'.result.json')).as_posix())
            state['wall_seconds']=round(time.monotonic()-started,3);save(root/'state.json',state)
            control=result.get('runtime_control',{})
            event(root,'meeting_finished',meeting_id=mid,status=result['status'],seconds=result.get('seconds'),
                attempts=control.get('attempted_calls'),charged_tokens=control.get('charged_tokens'),
                failed_stage=result.get('failed_stage'),reason=result.get('reason'))
            if result['status']!='COMPLETED':blocked_by=mid
    state.update(status='FINISHED',finished_at=utc(),wall_seconds=round(time.monotonic()-started,3));state.pop('current_meeting',None)
    save(root/'state.json',state);verify(root)
    event(root,'campaign_finished',completed=sum(r['status']=='COMPLETED' for r in state['meetings']),scheduled=16)

def progress(root):
    state=load(root/'state.json');mid=state.get('current_meeting');info={'status':state['status'],
        'completed':sum(r['status']=='COMPLETED' for r in state['meetings']),
        'failed':sum(r['status'] not in ('COMPLETED','RUNNING','SCHEDULED','BLOCKED_BY_PRIOR_FAILURE') for r in state['meetings']),
        'blocked':sum(r['status']=='BLOCKED_BY_PRIOR_FAILURE' for r in state['meetings']), 'current_meeting':mid}
    if mid:
        meta=root/'run/s2'/(mid+'.run.json')
        if meta.exists():
            m=load(meta);info.update(stage=m.get('current_stage'),checkpoints=len(m.get('checkpoints',{})))
        path=root/'run/s2'/(mid+'.stages.jsonl')
        if path.exists():
            events=[]
            for line in path.read_text(encoding='utf-8').splitlines():
                try:events.append(json.loads(line))
                except ValueError:pass # Live writer can be between writes; final audit requires exact JSONL.
            admitted=[e for e in events if e.get('event')=='request_admitted'];finished=[e for e in events if e.get('event')=='request_finished']
            info.update(attempts_admitted=len(admitted),attempts_finished=len(finished),
                known_charged_tokens=sum(e.get('charged_tokens',0) for e in finished),
                last_stage=next((e.get('current_stage') for e in reversed(events) if e.get('event')=='stage_started'),None))
    print(json.dumps(info,ensure_ascii=False),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['prepare','run','progress']);ap.add_argument('root',type=Path)
    args=ap.parse_args();globals()[args.command](args.root.resolve())
