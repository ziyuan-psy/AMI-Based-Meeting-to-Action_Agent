"""Replay one actual missing-time submission, then let the live model perform the bounded review."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').exists())
sys.path.insert(0,str(REPO/'agent/src'))
from mta import config
from mta.agent import MeetingBudget
from mta.control import RunControl
from mta.p2 import run_p2_agent
from mta.regression import RecordingLLM
from mta.store import Store
from mta.trace import Trace,StageTrace

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
source=ROOT/'fixed-v4'
config.DATA=source/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
mid='IS1008a';meta=load(source/'run/s2'/f'{mid}.run.json')
original=next(i for i in load(source/'run/s2'/f'{mid}.p2.json')['items'] if 'slides' in i['item_text'].lower())
raw=original['history'][0]['raw_tool_input']
candidate=load(Path(meta['checkpoints']['triaged']))[int(original['item_key'].split('#')[1])]
out=ROOT/(sys.argv[1] if len(sys.argv)>1 else 'deadline_review_probe')
out.mkdir()
request_trace=StageTrace(out/'requests.jsonl');trace=Trace(out/'trace.jsonl')
control=RunControl(trace=StageTrace(out/'control.jsonl'))
real=RecordingLLM(request_trace);scoped=real.scoped(control)

class FirstSubmission:
    p2_legacy_input=False
    first=True
    def create(self,*args,**kwargs):
        if self.first:
            self.first=False
            return SimpleNamespace(content=[SimpleNamespace(type='tool_use',id='captured_first',name='assess_item',input=copy.deepcopy(raw))])
        return scoped.create(*args,**kwargs)

save(out/'input.json',{'scope':'One captured actual assessment followed by a fresh model review; not a natural full run.',
    'captured_submission':raw,'candidate':candidate,'source_p2_sha256':hashlib.sha256((source/'run/s2'/f'{mid}.p2.json').read_bytes()).hexdigest()})
try:
    result=run_p2_agent(FirstSubmission(),Store(mid,allow_prior=False,run_dir=source/'run/s2'),candidate,
        MeetingBudget(),trace,original['item_key'],progress=lambda r:save(out/'checkpoint.json',r))
    save(out/'outcome.json',result)
    print(json.dumps({'state':result['state'],'recordable':result['recordable'],
        'deadline':result['fields'].get('deadline'),'actual_usage':real.usage_snapshot()},ensure_ascii=False))
finally:
    control.seal();trace.close();request_trace.finish(stage='probe',event='finished')
    save(out/'run.json',{'control':control.snapshot(),'actual_usage':real.usage_snapshot()})
