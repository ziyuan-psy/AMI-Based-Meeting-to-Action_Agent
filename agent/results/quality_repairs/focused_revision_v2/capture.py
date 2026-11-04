"""Online bounded-revision fault injection over real dialogue; not a natural extraction/eval case."""
import copy
import json
from pathlib import Path
import sys
REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path[:0]=[str(REPO/'agent/src'),str(REPO/'agent/logs/trace_step_deps')]
from mta import config
from mta.agent import MeetingBudget
from mta.control import RunControl
from mta.p2 import run_p2_agent
from mta.p2_admission import P2Admission
from mta.quality_study import verify
from mta.regression import RecordingLLM,route_inputs,load
from mta.store import Store
from mta.trace import StageTrace,Trace
from mta.observation import write_json,runtime_identity,sha256

ROOT=Path(__file__).resolve().parent
BUNDLE=ROOT.parent/'release-v8'
CAPTURE=ROOT.parent/'release-v7'
if (ROOT/'run.json').exists():raise FileExistsError('Never overwrite a capture')
verify(BUNDLE);route_inputs(config,BUNDLE)
source=next(i for i in load(CAPTURE/'after/IS1008a.normalized.json') if 'slides' in i['text'])
item=copy.deepcopy(source)
original=item['text']
item['text']='The project manager will send a copy of the slides to all attendees by Friday.'
item.update(flags=[],deadline='by Friday',deadline_basis='explicit',
    verification_request='This is a controlled fault injection: verify the trailing by Friday claim against delivered dialogue, which states the conclusion of the meeting. Preserve the real actor/task. You may choose the bounded revise_statement tool or remain pending; no unsupported time may be accepted.')
write_json(ROOT/'input.json',{'kind':'SYNTHETIC_UNSUPPORTED_SUFFIX_ON_REAL_DIALOGUE','source_text':original,'injected_item':item,
    'scope':'Tool/control validation only; excluded from all natural-case and F1 comparisons.'})
requests=StageTrace(ROOT/'requests.jsonl');trace=Trace(ROOT/'trace.jsonl')
client=RecordingLLM(requests);control=RunControl(trace=StageTrace(ROOT/'control.jsonl'))
admission=P2Admission(control,['IS1008a#injected'],3)
try:
    outcome=run_p2_agent(client.scoped(admission.view('IS1008a#injected')),Store('IS1008a',allow_prior=False,run_dir=CAPTURE/'after/s2'),
        item,MeetingBudget(),trace,'IS1008a#injected',progress=lambda d:write_json(ROOT/'checkpoint.json',d,control))
    revisions=[e for e in outcome['history'] if e.get('outcome')=='revised_requires_recheck']
    passed=(len(revisions)==1 and outcome['recordable'] and 'Friday' not in outcome['item_text'] and
            any(e.get('outcome')=='checked' and e.get('assessed_text')==outcome['item_text'] and e['step']>revisions[0]['step'] for e in outcome['history']))
    write_json(ROOT/'outcome.json',outcome)
    result={'status':'PASSED' if passed else 'NOT_DEMONSTRATED','scope':'Synthetic fault on real meeting evidence; no gold used; not an evaluation case.',
            'runtime':runtime_identity(client),'driver_sha256':sha256(Path(__file__)),'actual_api_usage':client.usage_snapshot(),
            'control':control.snapshot(),'admission':admission.snapshot(),'state':outcome['state'],'recordable':outcome['recordable'],
            'revised_text':outcome['item_text'],'successful_revisions':len(revisions)}
finally:
    control.seal();requests.finish(stage='capture',event='finished');trace.close()
write_json(ROOT/'run.json',result)
print(json.dumps({k:result[k] for k in ('status','state','recordable','revised_text','successful_revisions','actual_api_usage')},ensure_ascii=False))
