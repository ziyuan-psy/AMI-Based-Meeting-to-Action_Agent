"""Fixed-input live P2 comparison plus two independent historical-case repetitions. No gold."""
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
BEFORE=REPO/'agent/results/deadline_trace_audit/fixed-v5'

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def events(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]

def prepare(root):
    if root.exists():raise FileExistsError('New experiment directory required')
    sys.path.insert(0,str(REPO/'agent/src'))
    from mta import config
    from mta.llm import LLM
    from mta.observation import runtime_identity,source_files
    old=load(BEFORE/'study.json');runtime=runtime_identity(LLM())
    assert runtime['model']==old['runtime']['model']
    changes={k:{'before':old['runtime']['config'].get(k),'after':v} for k,v in runtime['config'].items() if v!=old['runtime']['config'].get(k)}
    assert changes=={'P2_PROTOCOL_VERSION':{'before':14,'after':19}},changes
    root.mkdir(parents=True);(root/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    shutil.copyfile(Path(__file__),root/'driver.py')
    shutil.copytree(BEFORE/'inputs',root/'inputs');shutil.copytree(BEFORE/'fixed_history',root/'fixed_history')
    for mid in old['meetings']:
        for stage in ('extracted','consolidated','selected','normalized','triaged'):
            shutil.copyfile(BEFORE/f'{mid}.{stage}.json',root/f'{mid}.{stage}.json')
    for p in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
        target=root/'source'/p.relative_to(REPO);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    artifact={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file()}
    save(root/'study.json',{'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Same captured candidates, transcript inputs and earlier record. New live P2/summary/report; original upstream budget reserved. Two separately reported same-version case repetitions. Not a fresh all-stage run or final evaluation.',
        'runtime':runtime,'configuration_changes':changes,'before':str(BEFORE),'upstream':old['upstream'],
        'meetings':old['meetings'],'case_repetitions':2,'gold_read':False,'driver_sha256':sha(Path(__file__)),
        'prior_history_sha256':sha(root/'fixed_history/TS3006a.json'),'artifact_sha256':artifact})
    print(json.dumps({'status':'PREPARED','root':str(root),'changes':changes},ensure_ascii=False),flush=True)

def initialize(root):
    study=load(root/'study.json');assert sha(Path(__file__))==study['driver_sha256']
    for rel,digest in study['artifact_sha256'].items():assert sha(root/rel)==digest,rel
    sys.path.insert(0,str(root/'source/agent/src'))
    from mta import config
    for k,v in study['runtime']['config'].items():setattr(config,k,v)
    config.DATA=root/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    return study,config

def run(root):
    study,config=initialize(root)
    from mta import pipeline
    from mta.p2_repair import RemainingBudgetLLM
    from mta.trace import StageTrace
    from mta.observation import runtime_identity
    out=root/'run'
    if out.exists():raise FileExistsError('Never replace an online cohort')
    (out/'s2').mkdir(parents=True);shutil.copyfile(root/'fixed_history/TS3006a.json',out/'s2/TS3006a.json')
    originals={name:getattr(pipeline,name) for name in ('extract_candidates','consolidate','select_items')}
    try:
        for mid in study['meetings']:
            for name,stage in (('extract_candidates','extracted'),('consolidate','consolidated'),('select_items','selected')):
                captured=load(root/f'{mid}.{stage}.json')
                def frozen(*args,_captured=captured,_stage=stage,**kwargs):
                    if kwargs.get('trace'):kwargs['trace'].log(stage=_stage,event='captured_upstream_input',reason='Fixed-input comparison; no upstream model call')
                    return copy.deepcopy(_captured)
                setattr(pipeline,name,frozen)
            trace=StageTrace(root/f'{mid}.requests.jsonl');client=RemainingBudgetLLM(trace,study['upstream'][mid])
            assert runtime_identity(client)['model']==study['runtime']['model']
            try:
                result=pipeline.run_meeting(config.TRANSCRIPTS/f'{mid}.txt','s2',client,out,out/'_cache')
                save(out/f'{mid}.result.json',result)
                print(json.dumps({'meeting_id':mid,'status':result['status'],
                    'new_calls':result['runtime_control']['attempted_calls'],'new_tokens':result['runtime_control']['charged_tokens'],
                    'seconds':result.get('seconds')},ensure_ascii=False),flush=True)
                if result['status']!='COMPLETED':raise SystemExit(1)
                meta=load(out/'s2'/f'{mid}.run.json')
                for stage in ('extracted','consolidated','selected','normalized','triaged'):
                    assert load(Path(meta['checkpoints'][stage]))==load(root/f'{mid}.{stage}.json'),stage
                assert sha(out/'s2/TS3006a.json')==study['prior_history_sha256']
            finally:trace.finish(stage='regression',event='finished')
    finally:
        for name,func in originals.items():setattr(pipeline,name,func)

def repeat(root):
    study,config=initialize(root)
    from mta.agent import MeetingBudget
    from mta.control import RunControl
    from mta.p2 import run_p2_agent
    from mta.p2_admission import P2Admission
    from mta.p2_repair import RemainingBudgetLLM
    from mta.store import Store
    from mta.trace import Trace,StageTrace
    candidate=load(root/'TS3006b.triaged.json')[10]
    old_case=next(i for i in load(BEFORE/'run/s2/TS3006b.p2.json')['items'] if i['item_key']=='TS3006b#10')
    candidate['peer_items']=old_case['initial_context']['peer_items']
    for n in range(1,study['case_repetitions']+1):
        out=root/f'case-repeat-{n}';out.mkdir()
        request_trace=StageTrace(out/'requests.jsonl');control_trace=StageTrace(out/'control.jsonl')
        control=RunControl(trace=control_trace);client=RemainingBudgetLLM(request_trace,study['upstream']['TS3006b'])
        client.scoped(control)  # Reserve the same original upstream calls/tokens/time first.
        admission=P2Admission(control,['TS3006b#10'],config.P2_RESERVED_COMPLETION_CALLS)
        trace=Trace(out/'trace.jsonl')
        save(out/'input.json',candidate)
        try:
            outcome=control.wait(__import__('mta.control',fromlist=['launch']).launch(lambda:
                run_p2_agent(client.scoped(admission.view('TS3006b#10')),Store('TS3006b',allow_prior=True,run_dir=root/'run/s2'),
                    candidate,MeetingBudget(),trace,'TS3006b#10',progress=lambda value:save(out/'checkpoint.json',value))))
            save(out/'outcome.json',outcome)
            print(json.dumps({'case_repeat':n,'state':outcome['state'],'related_items':outcome['fields']['related_items'],
                'calls':control.snapshot()['attempted_calls']},ensure_ascii=False),flush=True)
        finally:
            control.seal();trace.close();request_trace.finish(stage='case_repetition',event='finished')
            control_trace.finish(stage='case_repetition',event='finished')
            save(out/'run.json',{'control':control.snapshot(),'usage':client.usage_snapshot(),'admission':admission.snapshot()})

def run_all(root):
    run(root);repeat(root)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','run','repeat','run_all']);parser.add_argument('root',type=Path)
    args=parser.parse_args();globals()[args.command](args.root.resolve())
