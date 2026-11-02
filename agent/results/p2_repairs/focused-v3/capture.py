"""Focused live verification of three residual failure inputs. One shared original budget; no gold."""
from pathlib import Path
import copy,json,sys
from uuid import uuid4

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path[:0]=[str(REPO/'agent/src'),str(REPO/'agent/logs/trace_step_deps')]
from mta import config,pipeline
from mta.agent import MeetingBudget
from mta.control import RunControl,RunStopped
from mta.p2 import run_p2_agent,validate_outputs
from mta.regression import RecordingLLM,route_inputs,load
from mta.observation import runtime_identity,source_files,sha256,write_json,utc_now
from mta.store import Store
from mta.trace import StageTrace,Trace
from mta.triage import flag_items
from mta.validate import check

root=Path(__file__).resolve().parent
source=root.parent/'fresh-v1'
if (root/'run.json').exists():raise FileExistsError('Never overwrite a live capture')
(root/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
route_inputs(config,source)
stages=StageTrace(root/'requests.jsonl');trace=Trace(root/'agent.trace.jsonl')
client=RecordingLLM(stages);control=RunControl(trace=stages);scoped=client.scoped(control);budget=MeetingBudget()
metadata={'created_at':utc_now(),'status':'RUNNING','execution_kind':'focused_live_p2_stage_three_saved_inputs',
 'scope':'Residual protocol/routing verification; not a new whole-meeting run, no extraction/gold/judge.',
 'run_id':uuid4().hex,'source_study_sha256':sha256(source/'study.json'),'driver_sha256':sha256(Path(__file__)),
 'shared_limits':'All three inputs share one 80-call, 1,000,000-token, 1,200-second controller.',
 'runtime':runtime_identity(client),'cases':[]}
for path in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
 target=root/'source'/path.relative_to(REPO);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(path.read_bytes())
write_json(root/'run.json',metadata)
try:
 for mid,index in [('IS1008a',0),('TS3006b',0),('TS3006b',12)]:
  key=f'{mid}#{index}';stages.context['case']=key
  item=copy.deepcopy(load(source/'after'/(mid+'.normalized.json'))[index])
  previous=load(source/'after/s2'/(mid+'.p2.json'))['items'][index]
  item['peer_items']=previous['initial_context'].get('peer_items',[])
  store=Store(mid,allow_prior=True,run_dir=source/'after/s2');flag_items([item],store)
  item['run_id']=metadata['run_id'];folder=root/(mid+'-'+str(index));folder.mkdir()
  write_json(folder/'input.json',item)
  start=control.snapshot()['attempted_calls']
  result=run_p2_agent(scoped,store,item,budget,trace,key,lambda r:write_json(folder/'checkpoint.json',r,control))
  item['fields']=result['fields'];record=pipeline.make_record(mid,[item] if result['recordable'] else [],[])
  result['record_id']=record[0]['id'] if record else None
  for request in result['clarifications']:request['record_id']=result['record_id']
  p2={'format_version':1,'run_id':metadata['run_id'],'meeting_id':mid,'enabled':True,'items':[result]}
  questions={'format_version':1,'run_id':metadata['run_id'],'meeting_id':mid,'requests':result['clarifications']}
  errors=check(record,store)+validate_outputs(p2,questions,record,store)
  if errors:raise ValueError(errors)
  for name,value in [('record',record),('p2',p2),('clarifications',questions)]:write_json(folder/(name+'.json'),value)
  summary={'item_key':key,'state':result['state'],'recordable':result['recordable'],
    'termination_reason':result['termination_reason'],'calls':control.snapshot()['attempted_calls']-start,
    'invalid_actions':sum(e.get('outcome')=='invalid_action' for e in result['history']),
    'checks':{k:v['result'] for k,v in result['checks'].items()}}
  metadata['cases'].append(summary);write_json(root/'run.json',metadata)
  print(json.dumps(summary,ensure_ascii=False),flush=True)
 metadata['status']='COMPLETED'
except RunStopped as exc:
 metadata.update(status=exc.failure['status'],failure=exc.failure)
 raise
except Exception as exc:
 metadata.update(status='FAILED',error_type=type(exc).__name__)
 raise
finally:
 control.seal();stages.finish(stage='capture',event='finished',status=metadata['status'])
 metadata.update(finished_at=utc_now(),runtime_control=control.snapshot(),actual_new_usage=client.usage_snapshot())
 write_json(root/'run.json',metadata)
