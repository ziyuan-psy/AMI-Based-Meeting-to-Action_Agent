"""Fresh paired-input development acceptance for the smoke repairs. Never reads gold."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime,timezone

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
BEFORE=REPO/'agent/results/frozen_smoke/fresh-v1'
BASE_BUNDLE=REPO/'agent/results/quality_repairs/release-v9'

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def utc():return datetime.now(timezone.utc).isoformat()
def files(p):return sorted(f for f in p.rglob('*') if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc')

def prepare(root):
    if root.exists():raise FileExistsError('New experiment directory required')
    sys.path.insert(0,str(REPO/'agent/src'))
    from mta import config
    from mta.llm import LLM
    from mta.observation import runtime_identity,source_files
    from mta.item_selection import ranked_cap
    before=load(BEFORE/'study.json');runtime=runtime_identity(LLM())
    assert runtime['model']==before['expected_model']
    changes={k:{'before':before['expected_config'][k],'after':v} for k,v in runtime['config'].items() if v!=before['expected_config'][k]}
    assert set(changes)=={'P2_PROTOCOL_VERSION','ITEM_POLICY_VERSION'},changes
    protected=load(BEFORE/'protected_before.json')
    for rel,h in protected.items():assert sha(REPO/rel)==h
    for rel,h in load(BEFORE.parent/'manifest.json')['artifact_sha256'].items():assert sha(BEFORE.parent/rel)==h
    root.mkdir(parents=True);(root/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    shutil.copyfile(Path(__file__),root/'driver.py')
    shutil.copytree(BASE_BUNDLE/'inputs',root/'inputs')
    for p in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
        target=root/'source'/p.relative_to(REPO);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    save(root/'protected_before.json',protected)
    save(root/'study.json',{'created_at':utc(),'kind':'FRESH_LIVE_ALL_STAGES_SAME_INPUTS',
        'before':str(BEFORE),'base_commit':before['commit'],'branch':'agent-development','runtime':runtime,
        'config_changes':changes,'meetings':before['meetings'],'mode':'s2','response_cache_initially_empty':True,
        'upstream_reuse':False,'gold_used':False,'before_manifest_sha256':sha(BEFORE.parent/'manifest.json'),
        'driver_sha256':sha(Path(__file__)),
        'artifact_sha256':{**{p.relative_to(root).as_posix():sha(p) for folder in ['source','inputs'] for p in files(root/folder)},'driver.py':sha(root/'driver.py')}})
    # Replay the identical captured pool to isolate deterministic ranking from fresh model variation.
    es=[json.loads(s) for s in (BEFORE/'run/s2/IS1008a.stages.jsonl').read_text(encoding='utf-8').splitlines()]
    cap=next(e for e in es if e['stage']=='select' and e['event']=='cap')
    kept=ranked_cap(copy.deepcopy(cap['before']))
    save(root/'captured_cap_comparison.json',{'kind':'OFFLINE_IDENTICAL_CAPTURED_POOL','before_kept':[i['text'] for i in cap['kept']],
        'after_kept':[i['text'] for i in kept],'before_slides_kept':any('slides' in i['text'].lower() for i in cap['kept']),
        'after_slides_kept':any('slides' in i['text'].lower() for i in kept),'unchanged_limits':config.MAX_ITEMS})
    assert any('slides' in i['text'].lower() for i in kept)
    print(json.dumps({'status':'PREPARED','root':str(root),'model':runtime['model'],'config_changes':changes},ensure_ascii=False))

def initialize(root):
    study=load(root/'study.json')
    if 'driver_sha256' in study:assert sha(Path(__file__))==study['driver_sha256']
    for rel,h in study['artifact_sha256'].items():assert sha(root/rel)==h,rel
    for rel,h in load(BEFORE.parent/'manifest.json')['artifact_sha256'].items():assert sha(BEFORE.parent/rel)==h,rel
    assert sha(BEFORE.parent/'manifest.json')==study['before_manifest_sha256']
    sys.path.insert(0,str(root/'source/agent/src'))
    from mta import config
    for k,v in study['runtime']['config'].items():setattr(config,k,v)
    config.DATA=root/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    return study,config

def recorder(path):
    from mta.llm import LLM
    from mta.trace import StageTrace
    trace=StageTrace(path)
    class RecordingLLM(LLM):
        def _create(self,system,messages,tools=None,tool_choice=None,max_tokens=4096,effort=None,stage=None,control=None):
            trace.log(stage=stage or 'other',event='model_request',system=system,messages=messages,
                tools=tools,tool_choice=tool_choice,max_tokens=max_tokens,effort=effort)
            return super()._create(system,messages,tools,tool_choice,max_tokens,effort,stage,control)
    return RecordingLLM(),trace

def run(root):
    study,config=initialize(root)
    from mta.pipeline import run_meeting
    from mta.observation import runtime_identity
    out=root/'run'
    if out.exists():raise FileExistsError('Never resume or replace a fresh primary run')
    out.mkdir()
    llm,requests=recorder(root/'requests.jsonl')
    # A recording subclass does not change the underlying model/transport.
    assert runtime_identity(llm)['model']==study['runtime']['model']
    try:
        for mid in study['meetings']:
            result=run_meeting(config.TRANSCRIPTS/(mid+'.txt'),'s2',llm,out,out/'_cache')
            save(out/(mid+'.result.json'),result)
            print(json.dumps({'meeting_id':mid,'status':result['status'],'calls':result['runtime_control']['attempted_calls']},ensure_ascii=False),flush=True)
            if result['status']!='COMPLETED':raise SystemExit(1)
    finally:requests.finish(stage='study',event='finished')

def probe(root):
    study,config=initialize(root)
    from mta.agent import MeetingBudget
    from mta.control import RunControl
    from mta.p2 import run_p2_agent
    from mta.p2_admission import P2Admission
    from mta.store import Store
    from mta.trace import Trace,StageTrace
    from mta.observation import write_json
    out=root/'revision_probe'
    if out.exists():raise FileExistsError('Never replace a probe')
    out.mkdir()
    item=load(BEFORE.parent/'revision-probe-online-v1/input.json')['injected_item']
    save(out/'input.json',{'kind':'SAME_CONTROLLED_INPUT_AS_BEFORE','injected_item':item,'not_a_natural_case':True})
    llm,requests=recorder(out/'requests.jsonl');control=RunControl(trace=StageTrace(out/'control.jsonl'))
    trace=Trace(out/'trace.jsonl');key='IS1008a#controlled-friday';admission=P2Admission(control,[key],3)
    try:
        result=run_p2_agent(llm.scoped(admission.view(key)),Store('IS1008a',allow_prior=False,run_dir=root/'run/s2'),
            item,MeetingBudget(),trace,key,progress=lambda d:write_json(out/'checkpoint.json',d,control))
        save(out/'outcome.json',result)
        print(json.dumps({'state':result['state'],'recordable':result['recordable'],'text':result['item_text'],'actual_usage':llm.usage_snapshot()},ensure_ascii=False))
    finally:
        control.seal();trace.close();requests.finish(stage='probe',event='finished')
        save(out/'run.json',{'control':control.snapshot(),'actual_usage':llm.usage_snapshot(),'admission':admission.snapshot()})

def run_with_probe(root):
    run(root)
    probe(root)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','run','probe','run_with_probe']);p.add_argument('root',type=Path)
    a=p.parse_args();globals()[a.command](a.root.resolve())
