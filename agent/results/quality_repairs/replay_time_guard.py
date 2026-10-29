"""Replay the FIRST real faulty Friday submission through the final source. Zero API calls."""
from pathlib import Path
import copy,json,sys
from types import SimpleNamespace as NS
REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path[:0]=[str(REPO/'agent/src'),str(REPO/'agent/logs/trace_step_deps')]
from mta import config
from mta.agent import MeetingBudget
from mta.p2 import run_p2_agent
from mta.regression import route_inputs,load
from mta.store import Store
from mta.trace import Trace
from mta.observation import write_json,sha256

ROOT=Path(__file__).resolve().parent
source=ROOT/'focused_revision'
original=load(source/'outcome.json')
payload=next(e['raw_tool_input'] for e in original['history'] if e.get('outcome')=='checked')
item=load(source/'input.json')['injected_item']
route_inputs(config,ROOT/'release-v9')
class Replay:
    p2_legacy_input=False
    def __init__(self):self.calls=0
    def create(self,*args,**kwargs):
        self.calls+=1
        name,value=('assess_item',payload) if self.calls==1 else ('stop',{'reason':'End of original-response replay; no additional model judgment fabricated'})
        return NS(content=[NS(type='tool_use',name=name,id='replay-'+str(self.calls),input=copy.deepcopy(value))])
client=Replay()
result=run_p2_agent(client,Store('IS1008a',allow_prior=False,run_dir=ROOT/'release-v9/after/s2'),item,MeetingBudget(),Trace(),'IS1008a#injected')
checked=next(e for e in result['history'] if e.get('outcome')=='checked')
assert original['recordable'] and not result['recordable']
assert checked['checks']['statement']['reason'].startswith('unanchored_time_literal:')
write_json(ROOT/'time_guard_replay.json',{'status':'PASSED','actual_api_calls':0,'policy_version':config.P2_PROTOCOL_VERSION,
    'scope':'Original real model submission on a synthetic suffix over real dialogue. Same payload/evidence, no new positive judgment. Not a natural evaluation case.',
    'original_recordable':original['recordable'],'final_recordable':result['recordable'],
    'original_input_sha256':sha256(source/'input.json'),'original_outcome_sha256':sha256(source/'outcome.json'),
    'checked_finding':checked['checks']['statement'],'outcome':result,'driver_sha256':sha256(Path(__file__))})
print(json.dumps({'status':'PASSED','actual_api_calls':0,'old_recordable':True,'new_recordable':False},ensure_ascii=False))
