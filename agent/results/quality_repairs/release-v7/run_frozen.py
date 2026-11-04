"""Run the frozen source on the frozen development inputs with a fresh, original budget.

This is a new online run, not the cached repair experiment. No gold is loaded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

BUNDLE=Path(__file__).resolve().parent
sys.path.insert(0,str(BUNDLE/'source/agent/src'))
from mta import config
from mta.baseline import check_selection,check_destination
from mta.llm import LLM
from mta.observation import runtime_identity
from mta.pipeline import run_meeting

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();destination=args.output.resolve()
    study=json.loads((BUNDLE/'study.json').read_text(encoding='utf-8'))
    for rel,expected in study['artifact_sha256'].items():
        if rel.startswith(('source/','inputs/')):
            actual=hashlib.sha256((BUNDLE/rel).read_bytes()).hexdigest()
            if actual!=expected:raise ValueError('Frozen source/input changed: '+rel)
    check_selection(study['meetings'],['s2']);check_destination(destination)
    for name,value in study['runtime']['config'].items():setattr(config,name,value)
    config.DATA=BUNDLE/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    llm=LLM()
    if runtime_identity(llm)['model']!=study['runtime']['model']:
        raise ValueError('Use the recorded model/endpoint; provide credentials in the environment only')
    destination.mkdir(parents=True)
    for mid in study['meetings']:
        result=run_meeting(config.TRANSCRIPTS/(mid+'.txt'),'s2',llm,destination,destination/'_cache')
        (destination/(mid+'.result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'meeting_id':mid,'status':result['status'],'output':result.get('output'),
                          'calls':result.get('runtime_control',{}).get('attempted_calls')},ensure_ascii=False),flush=True)
        if result['status']!='COMPLETED':raise SystemExit(1)

if __name__=='__main__':main()
