"""Replay actual residual responses; no new model judgment and zero API calls."""
from pathlib import Path
from types import SimpleNamespace as NS
import copy,json,sys

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path[:0]=[str(REPO/'agent/src'),str(REPO/'agent/logs/trace_step_deps')]
from jsonschema import Draft202012Validator
from mta import config,pipeline
from mta.assessment_protocol import compact_tool
from mta.p2 import run_p2_agent,validate_outputs
from mta.agent import MeetingBudget
from mta.regression import route_inputs,load
from mta.store import Store
from mta.trace import Trace
from mta.triage import flag_items
from mta.validate import check
from mta.observation import write_json,sha256,utc_now

root=Path(__file__).resolve().parent;source=root.parent/'fresh-v1'
route_inputs(config,source)
class Replay:
 def __init__(self,steps):self.steps=list(steps)
 def create(self,*args,**kwargs):
  name,payload=self.steps.pop(0)
  return NS(content=[NS(type='tool_use',id='saved-choice',name=name,input=copy.deepcopy(payload))])

def prefix_through(mid,text,target):
 conversations=[]
 for line in (source/'after'/(mid+'.requests.jsonl')).read_text(encoding='utf-8').splitlines():
  event=json.loads(line)
  if event.get('event')!='model_request':continue
  messages=event['messages']
  try:initial=json.loads(messages[0]['content'])
  except (json.JSONDecodeError,TypeError):continue
  if initial.get('item',{}).get('text')==text:conversations.append(messages)
 messages=max(conversations,key=len)
 steps=[]
 for message in messages:
  if message.get('role')!='assistant' or not isinstance(message.get('content'),list):continue
  uses=[b for b in message['content'] if b.get('type')=='tool_use']
  if uses:
   pair=(uses[0]['name'],uses[0]['input']);steps.append(pair)
   if pair==target:return steps
 return steps+[target]

results=[]
for mid,index,reason in [('IS1008a',0,'affected_fields'),('TS3006b',12,'confidence'),('TS3006b',0,'routing')]:
 original=load(source/'after/s2'/(mid+'.p2.json'))['items'][index]
 item=copy.deepcopy(load(source/'after'/(mid+'.normalized.json'))[index])
 item['peer_items']=original['initial_context'].get('peer_items',[])
 store=Store(mid,allow_prior=True,run_dir=source/'after/s2');flag_items([item],store)
 if reason!='routing':
  require_prior=store.order>1 and bool({'POSSIBLE_PRIOR','STATUS_CHECK'}&set(item['flags']))
  schema=compact_tool(store.order>1,item['type'],require_prior)['input_schema']
  candidates=[]
  for line in (source/'after/s2'/(mid+'.stages.jsonl')).read_text(encoding='utf-8').splitlines():
   for tool in json.loads(line).get('response_tools',[]):
    if tool['name']!='assess_item':continue
    errors=list(Draft202012Validator(schema).iter_errors(tool['input']))
    if len(errors)==1 and reason in errors[0].message:candidates.append(tool)
  assert len(candidates)==1,(mid,index,len(candidates))
  steps=prefix_through(mid,item['text'],(candidates[0]['name'],candidates[0]['input']))
 else:
  checked=next(h for h in original['history'] if h.get('outcome')=='checked')
  first=checked['raw_tool_input']
  lookups={}
  for line in (source/'after'/(mid+'.requests.jsonl')).read_text(encoding='utf-8').splitlines():
   event=json.loads(line)
   if event.get('event')!='model_request':continue
   messages=event['messages']
   try:initial=json.loads(messages[0]['content'])
   except (json.JSONDecodeError,TypeError):continue
   if initial.get('item',{}).get('text')!=item['text']:continue
   for msg in messages:
    if msg.get('role')!='assistant' or not isinstance(msg.get('content'),list):continue
    for block in msg['content']:
     if block.get('type')=='tool_use' and block['name']=='read_dialogue' and 'meeting_id' not in block['input']:
      lookups[block['id']]=block['input']
  assert len(lookups)==1,(mid,index,lookups)
  steps=prefix_through(mid,item['text'],('read_dialogue',next(iter(lookups.values()))))
 original_steps=config.MAX_AGENT_STEPS
 try:
  config.MAX_AGENT_STEPS=len(steps)
  outcome=run_p2_agent(Replay(steps),store,item,MeetingBudget(),Trace(),f'{mid}#{index}')
 finally:config.MAX_AGENT_STEPS=original_steps
 item['fields']=outcome['fields'];record=pipeline.make_record(mid,[item] if outcome['recordable'] else [],[])
 outcome['record_id']=record[0]['id'] if record else None
 for question in outcome['clarifications']:question['record_id']=outcome['record_id']
 p2={'format_version':1,'run_id':'one-response-replay','meeting_id':mid,'enabled':True,'items':[outcome]}
 questions={'format_version':1,'run_id':'one-response-replay','meeting_id':mid,'requests':outcome['clarifications']}
 assert check(record,store)+validate_outputs(p2,questions,record,store)==[]
 assert outcome['history'][-2].get('outcome')!='invalid_action'
 if reason=='affected_fields':assert not outcome['recordable']  # Original response also selected an unread A.10; never auto-correct it to C.10.
 if reason=='confidence':assert any('ignored_advisory_confidence' in h.get('protocol_normalizations',[]) for h in outcome['history'])
 if reason=='routing':assert any(h.get('action')=='read_dialogue' and h.get('outcome')=='lookup' for h in outcome['history'])
 results.append({'item_key':f'{mid}#{index}','residual':reason,'actual_api_calls':0,
   'source_trace_sha256':sha256(source/'after/s2'/(mid+'.stages.jsonl')),
   'replayed_choices':steps,'outcome':outcome,'record':record,
   'scope':'Bounded replay of existing assessment/tool choices, not a new live run or human semantic validation.'})
write_json(root/'replay.json',{'created_at':utc_now(),'actual_api_calls':0,'policy_version':config.P2_PROTOCOL_VERSION,'results':results})
print(json.dumps([{'case':r['item_key'],'recordable':r['outcome']['recordable'],
 'unresolved':{k:v['reason'] for k,v in r['outcome']['checks'].items() if v['result'] not in ('pass','not_applicable')}} for r in results],ensure_ascii=False))
