"""Reprocess the last real provider tool arguments after the lossless redundancy fix. Zero API."""
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
ONLINE=ROOT/'fixed-v5'
OUT=ROOT/(sys.argv[1] if len(sys.argv)>1 else 'final-response-replay-v3')
sys.path.insert(0,str(REPO/'agent/src'))

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    from mta import config
    from mta.agent import MeetingBudget
    from mta.control import Limits
    from mta.observation import source_files
    from mta.p2 import run_p2_agent,validate_outputs
    from mta.p2_admission import ItemAllowanceExhausted
    from mta.store import Store
    from mta.trace import Trace
    from mta.validate import check
    from mta.render import render_report
    if OUT.exists():raise FileExistsError('Do not overwrite a response replay')
    OUT.mkdir();(OUT/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    shutil.copytree(ONLINE/'inputs',OUT/'inputs');shutil.copytree(ONLINE/'fixed_history',OUT/'prior_records')
    shutil.copyfile(Path(__file__),OUT/'driver.py')
    source={}
    for p in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
        target=OUT/'source'/p.relative_to(REPO);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
        source[p.relative_to(REPO).as_posix()]=sha(p)
    config.DATA=OUT/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    raw=load(ONLINE/'summary.json');results=[]
    save(OUT/'study.json',{'created_at':datetime.now(timezone.utc).isoformat(),'execution_kind':'ZERO_API_CAPTURED_TOOL_ARGUMENT_REPLAY',
        'actual_api_calls':0,'original_online_source':str(ONLINE),'source_sha256':source,
        'p2_protocol_version':config.P2_PROTOCOL_VERSION,'gold_scored':False,
        'scope':'Same actual model selections, task, transcripts and prior record. No new source, model verdict, link label or ID is authored.'})
    for original in raw['case_runs']:
        previous=original['outcome'];folder=OUT/original['source'];folder.mkdir()
        candidate=load(ONLINE/'TS3006b.triaged.json')[10]
        candidate['peer_items']=previous['initial_context']['peer_items']
        stream=[]
        for event in previous['history']:
            if 'raw_tool_input' in event:stream.append((event['action'],event['raw_tool_input']))
            elif event.get('outcome')=='lookup':stream.append((event['action'],event['args']))
        class Captured:
            p2_legacy_input=False
            consumed=0
            def create(self,*args,**kwargs):
                if self.consumed>=len(stream):raise ItemAllowanceExhausted('No new model response invented')
                name,payload=stream[self.consumed];self.consumed+=1
                return SimpleNamespace(content=[SimpleNamespace(type='tool_use',id='capture_'+str(self.consumed),name=name,input=copy.deepcopy(payload))])
        store=Store('TS3006b',allow_prior=True,run_dir=OUT/'prior_records')
        client=Captured();trace=Trace(folder/'trace.jsonl')
        outcome=run_p2_agent(client,store,candidate,MeetingBudget(),trace,'TS3006b#10',progress=lambda v:save(folder/'checkpoint.json',v))
        trace.close();save(folder/'outcome.json',outcome)
        record_id=previous.get('record_id') or 'TS3006b.sys.s.1'
        indexed=copy.deepcopy(outcome);indexed['record_id']=record_id if indexed['recordable'] else None
        for request in indexed['clarifications']:request['record_id']=indexed['record_id']
        records=[{'id':record_id,'text':indexed['item_text'],'type':indexed['item_type'],**indexed['fields']}] if indexed['recordable'] else []
        p2={'format_version':1,'run_id':'actual-response-replay','meeting_id':'TS3006b','enabled':True,'items':[indexed]}
        qs={'format_version':1,'run_id':'actual-response-replay','meeting_id':'TS3006b','requests':indexed['clarifications']}
        assert not (errors:=check(records,store)+validate_outputs(p2,qs,records,store)),errors
        save(folder/'TS3006b.json',records);save(folder/'TS3006b.p2.json',p2);save(folder/'TS3006b.clarifications.json',qs)
        (folder/'TS3006b_report.md').write_text(render_report(records,store,'s2',{'scope':'Zero API captured-response replay'},p2),encoding='utf-8')
        results.append({'source':original['source'],'before_links':previous['fields']['related_items'],
            'after_links':outcome['fields']['related_items'],'captured_steps_available':len(stream),
            'captured_steps_consumed':client.consumed,'outcome':outcome,'validation_errors':errors})
    assert results[0]['after_links']==[{'id':'TS3006a.sys.s.4','relation':'updates'}]
    assert any('identical_root_relation_selection' in e.get('protocol_normalizations',[]) for e in results[0]['outcome']['history'])
    assert sha(OUT/'prior_records/TS3006a.json')==sha(ONLINE/'fixed_history/TS3006a.json')
    save(OUT/'summary.json',{'result':'TARGETED_REPLAY_PASSED','actual_api_calls':0,'cases':results,
        'original_online_cases_unchanged':True,'not_a_new_live_sampling_run':True,'gold_scored':False})
    print(json.dumps({'result':'TARGETED_REPLAY_PASSED','actual_api_calls':0,
        'cases':[{k:v for k,v in r.items() if k!='outcome'} for r in results]},ensure_ascii=False))

if __name__=='__main__':main()
