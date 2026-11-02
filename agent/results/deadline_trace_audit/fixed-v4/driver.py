"""Fixed upstream live P2 regression and a separate same-request extraction probe. No gold."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
REPO = next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
BEFORE = REPO/'agent/results/smoke_repairs/repair-v5'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def events(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def prepare(root):
    if root.exists():
        raise FileExistsError('New experiment directory required')
    sys.path.insert(0, str(REPO/'agent/src'))
    from mta import config
    from mta.llm import LLM
    from mta.observation import runtime_identity, source_files
    before = load(BEFORE/'study.json')
    runtime = runtime_identity(LLM())
    assert runtime['model'] == before['runtime']['model']
    changes = {k: {'before': before['runtime']['config'].get(k), 'after': v}
               for k,v in runtime['config'].items() if v != before['runtime']['config'].get(k)}
    assert changes == {'P2_PROTOCOL_VERSION': {'before':9, 'after':12}}, changes
    root.mkdir(parents=True)
    (root/'.gitattributes').write_text('* -text -whitespace\n', encoding='utf-8')
    shutil.copytree(BEFORE/'inputs', root/'inputs')
    shutil.copyfile(Path(__file__), root/'driver.py')
    for p in source_files()+[config.SCHEMA, config.P2_SCHEMA]:
        target=root/'source'/p.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p,target)
    upstream={}
    for mid in ('IS1008a','TS3006b'):
        meta=load(BEFORE/'run/s2'/f'{mid}.run.json')
        for stage in ('extracted','consolidated','selected','normalized','triaged'):
            shutil.copyfile(Path(meta['checkpoints'][stage]), root/f'{mid}.{stage}.json')
        attempts=[a for a in meta['runtime_control']['attempts']
                  if a['stage'] in ('extract','consolidate','decision_consolidate','select')]
        es=events(BEFORE/'run/s2'/f'{mid}.stages.jsonl')
        start=next(e['t'] for e in es if e['stage']=='run' and e['event']=='started')
        end=next(e['t'] for e in es if e['stage']=='triage' and e['event']=='completed')
        upstream[mid]={'attempts':len(attempts), 'charged_tokens':sum(a['charged_tokens'] for a in attempts),
                       'elapsed_seconds':round(end-start,3)}
    prior=root/'fixed_history';prior.mkdir()
    shutil.copyfile(BEFORE/'run/s2/TS3006a.json',prior/'TS3006a.json')
    # Keep the exact earlier publication as fixed input; it is not represented as a new model run.
    requests=events(BEFORE/'requests.jsonl')
    probe=next(e for e in requests if e.get('stage')=='extract'
               and 'at the conclusion of our meeting' in json.dumps(e.get('messages',[]))
               and 'IS1008a.C.dialog-act' not in json.dumps(e.get('messages',[])))
    # Extraction uses integer evidence locators, so identify the same original request by its text.
    save(root/'extraction_request.json', {k:probe[k] for k in ('messages','tools','tool_choice','effort')})
    artifacts={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file()}
    save(root/'study.json', {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Captured identical upstream candidates and TS3006a history; new live P2, validation, report and summary. Original upstream calls/tokens/time reserved. Separate same-user-request extraction probe. Not a fresh all-stage run or evaluation.',
        'runtime':runtime,'configuration_changes':changes,'upstream':upstream,
        'meetings':['IS1008a','TS3006b'],'prior_history_sha256':sha(prior/'TS3006a.json'),
        'gold_read':False,'driver_sha256':sha(Path(__file__)),'artifact_sha256':artifacts})
    print(json.dumps({'status':'PREPARED','root':str(root),'upstream_reserved':upstream}, ensure_ascii=False),flush=True)


def initialize(root):
    study=load(root/'study.json')
    assert sha(Path(__file__))==study['driver_sha256']
    for rel,digest in study['artifact_sha256'].items():
        assert sha(root/rel)==digest,rel
    sys.path.insert(0,str(root/'source/agent/src'))
    from mta import config
    for k,v in study['runtime']['config'].items():
        setattr(config,k,v)
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
    if out.exists():
        raise FileExistsError('Never replace a real regression attempt')
    (out/'s2').mkdir(parents=True)
    shutil.copyfile(root/'fixed_history/TS3006a.json',out/'s2/TS3006a.json')
    original={name:getattr(pipeline,name) for name in ('extract_candidates','consolidate','select_items')}
    try:
        for mid in study['meetings']:
            for name,stage in (('extract_candidates','extracted'),('consolidate','consolidated'),('select_items','selected')):
                captured=load(root/f'{mid}.{stage}.json')
                def frozen(*args,_captured=captured,_stage=stage,**kwargs):
                    trace=kwargs.get('trace')
                    if trace:
                        trace.log(stage=_stage,event='captured_upstream_input',reason='Identical fixed-input P2 comparison; no new upstream API call')
                    return copy.deepcopy(_captured)
                setattr(pipeline,name,frozen)
            trace=StageTrace(root/f'{mid}.requests.jsonl')
            llm=RemainingBudgetLLM(trace,study['upstream'][mid])
            assert runtime_identity(llm)['model']==study['runtime']['model']
            try:
                result=pipeline.run_meeting(config.TRANSCRIPTS/f'{mid}.txt','s2',llm,out,out/'_cache')
                save(out/f'{mid}.result.json',result)
                print(json.dumps({'meeting_id':mid,'status':result['status'],
                    'new_calls':result['runtime_control']['attempted_calls'],
                    'new_tokens':result['runtime_control']['charged_tokens'],
                    'seconds':result.get('seconds')},ensure_ascii=False),flush=True)
                if result['status']!='COMPLETED':
                    raise SystemExit(1)
                meta=load(out/'s2'/f'{mid}.run.json')
                for stage in ('extracted','consolidated','selected','normalized','triaged'):
                    assert load(Path(meta['checkpoints'][stage]))==load(root/f'{mid}.{stage}.json'),stage
                assert sha(out/'s2/TS3006a.json')==study['prior_history_sha256']
            finally:
                trace.finish(stage='fixed_p2_regression',event='finished')
    finally:
        for name,func in original.items():
            setattr(pipeline,name,func)


def extract_probe(root):
    study,config=initialize(root)
    from mta.control import RunControl
    from mta.extract import CANDIDATE_SCHEMA, _clean
    from mta.prompts import EXTRACT_SYSTEM
    from mta.regression import RecordingLLM
    from mta.trace import StageTrace
    from mta.transcript_parser import parse_transcript_file
    out=root/'extraction_probe'
    out.mkdir()
    trace=StageTrace(out/'trace.jsonl');control=RunControl(trace=trace)
    client=RecordingLLM(trace);llm=client.scoped(control)
    request=load(root/'extraction_request.json')
    try:
        raw=llm.forced_tool(EXTRACT_SYSTEM,request['messages'][0]['content'],
            'submit_candidates',CANDIDATE_SCHEMA,effort=request['effort'],stage='extract')
        acts=parse_transcript_file(config.TRANSCRIPTS/'IS1008a.txt')
        candidates=[]
        for row in raw.get('items',[]):
            ev=[acts[i]['id'] for i in row.get('evidence',[]) if isinstance(i,int) and 0<=i<len(acts)]
            if ev and (candidate:=_clean(row,ev)):
                candidates.append(candidate)
        save(out/'result.json',{'kind':'SAME_CAPTURED_USER_REQUEST_NEW_PROMPT_LIVE_EXTRACTION',
            'source_request_sha256':sha(root/'extraction_request.json'),'raw':raw,'candidates':candidates})
        print(json.dumps({'extraction_probe':'COMPLETED','slides':[r for r in candidates if 'slides' in r['text'].lower()]},ensure_ascii=False),flush=True)
    finally:
        control.seal();trace.finish(stage='extraction_probe',event='finished')
        save(out/'control.json',control.snapshot())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','run','extract_probe'])
    parser.add_argument('root',type=Path)
    args=parser.parse_args()
    globals()[args.command](args.root.resolve())
