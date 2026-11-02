"""Replay actual saved tool arguments against old/new controllers, with no provider or gold."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
BEFORE=REPO/'agent/results/smoke_repairs/repair-v5'


def load(p):
    return json.loads(p.read_text(encoding='utf-8'))


def run_engine(package_name, source, key):
    package=source/'agent/src/mta'
    spec=importlib.util.spec_from_file_location(package_name,package/'__init__.py',submodule_search_locations=[str(package)])
    module=importlib.util.module_from_spec(spec);sys.modules[package_name]=module;spec.loader.exec_module(module)
    def mod(name):
        return __import__(package_name+'.'+name,fromlist=[name])
    config=mod('config')
    config.DATA=BEFORE/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    store=mod('store').Store('TS3006b',allow_prior=True,run_dir=BEFORE/'run/s2')
    meta=load(BEFORE/'run/s2/TS3006b.run.json')
    candidate=load(Path(meta['checkpoints']['triaged']))[int(key.split('#')[1])]
    checkpoint=load(Path(meta['checkpoints']['p2_item_'+key.split('#')[1]]))
    steps=[]
    for e in checkpoint['history']:
        if 'raw_tool_input' in e:
            steps.append((e['action'],e['raw_tool_input']))
        elif e.get('outcome')=='lookup':
            steps.append((e['action'],e['args']))
    # A terminal fallback is only used if the new controller offers a review/choice absent in old output.
    steps.append(('stop',{'reason':'Replay exhausted; no new model decision or evidence is invented'}))
    class Saved:
        p2_legacy_input=False
        def __init__(self):
            self.calls=0
        def create(self,*args,**kwargs):
            name,payload=steps[self.calls];self.calls+=1
            return SimpleNamespace(content=[SimpleNamespace(type='tool_use',id='r'+str(self.calls),name=name,input=copy.deepcopy(payload))])
    model=Saved();budget=mod('agent').MeetingBudget()
    outcome=mod('p2').run_p2_agent(model,store,candidate,budget,mod('trace').Trace(),key)
    return {'model_decisions_replayed':model.calls,'lookups_charged':budget.used,'outcome':outcome}


def main():
    results={}
    for n,key in enumerate(('TS3006b#0','TS3006b#3','TS3006b#11')):
        before=run_engine('saved_before_'+str(n),BEFORE/'source',key)
        after=run_engine('saved_after_'+str(n),REPO,key)
        results[key]={'before':before,'after':after}
    assert results['TS3006b#0']['after']['lookups_charged'] < results['TS3006b#0']['before']['lookups_charged']
    assert results['TS3006b#11']['after']['lookups_charged'] < results['TS3006b#11']['before']['lookups_charged']
    assert results['TS3006b#3']['after']['model_decisions_replayed'] < results['TS3006b#3']['before']['model_decisions_replayed']
    out={'kind':'EXACT_CAPTURED_TOOL_ARGUMENT_REPLAY_NO_API','results':results,'gold_read':False}
    (ROOT/'captured_replay.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:{side:{k:v for k,v in run.items() if k!='outcome'} for side,run in pair.items()}
        for key,pair in results.items()},ensure_ascii=False))


if __name__=='__main__':
    main()
