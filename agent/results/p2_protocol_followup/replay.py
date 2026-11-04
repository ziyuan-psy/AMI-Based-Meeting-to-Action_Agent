"""Same raw submissions against saved schemas/controllers; no API, gold, or invented outcomes."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
OLD=REPO/'agent/results/deadline_trace_audit/fixed-v5'
V9=REPO/'agent/results/smoke_repairs/repair-v5'

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def package(name,root):
    path=root/'agent/src/mta'
    spec=importlib.util.spec_from_file_location(name,path/'__init__.py',submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return lambda child:__import__(name+'.'+child,fromlist=[child])

def main():
    from jsonschema import Draft202012Validator
    observed=load(ROOT/'observed.json')
    settings=[('v9',package('replay_v9',V9/'source')),('v14',package('replay_v14',OLD/'source')),('current',package('replay_now',REPO))]
    before_items=load(OLD/'run/s2/TS3006b.p2.json')['items']
    meta=load(OLD/'run/s2/TS3006b.run.json')
    before_items+=load(Path(meta['checkpoints']['verified_omitted']))['outcomes']
    type_by_key={i['item_key']:i['item_type'] for i in before_items}
    cands=load(OLD/'TS3006b.triaged.json')
    errors=[]
    for event in observed['invalid_submissions']:
        row={'item_key':event['item_key'],'tool':event['action'],'captured_error':event['error']}
        if not event['action'].startswith('assess_'):
            row['classification']='query_rule_not_schema';errors.append(row);continue
        n=int(event['item_key'].split('#')[1]);candidate=cands[n]
        for name,mod in settings:
            raw=copy.deepcopy(event['raw_tool_input']);normalizations=[]
            if name=='current':raw,normalizations=mod('assessment_protocol').normalize_submission(raw,event['action'])
            if event['action']=='assess_item':
                raw,_=mod('assessment_protocol').complete_inactive_metadata(raw,no_prior_scope=False)
                schema=mod('assessment_protocol').compact_tool(True,type_by_key[event['item_key']],
                    bool({'POSSIBLE_PRIOR','STATUS_CHECK'}&set(candidate['flags'])))['input_schema']
            else:
                schema=mod('p2').relation_tool()['input_schema']
            row[name]={'schema_errors':['/'.join(map(str,e.absolute_path))+': '+e.message for e in Draft202012Validator(schema).iter_errors(raw)],
                       'normalizations':normalizations}
        errors.append(row)
    schema_rows=[r for r in errors if 'current' in r]
    same_contracts=all(r['v9']['schema_errors']==r['v14']['schema_errors'] for r in schema_rows)
    result={'same_raw_submissions':errors,'v9_v14_rejection_equivalence':same_contracts,
        'same_initial_context_v9_v14':observed['cross_meeting_case'][0]['initial_context']==observed['cross_meeting_case'][1]['initial_context'],
        'v9_v14_system_prompt_identical':settings[0][1]('prompts').P2_SYSTEM==settings[1][1]('prompts').P2_SYSTEM,
        'before_invalid_submissions':len(errors),'query_rejections':len(errors)-len(schema_rows),
        'before_schema_rejections':sum(bool(r['v14']['schema_errors']) for r in schema_rows),
        'after_schema_rejections':sum(bool(r['current']['schema_errors']) for r in schema_rows)}
    case=observed['cross_meeting_case'][1]
    raw=case['history'][0]['raw_tool_input'];candidate=copy.deepcopy(cands[10])
    branch=[]
    for name,mod in settings[1:]:
        cfg=mod('config');cfg.DATA=OLD/'inputs';cfg.TRANSCRIPTS=cfg.DATA/'transcripts'
        cfg.PARTICIPANTS=cfg.DATA/'participants.json';cfg.SCENARIO=cfg.DATA/'scenario_text.txt'
        store=mod('store').Store('TS3006b',allow_prior=True,run_dir=OLD/'run/s2')
        class Saved:
            p2_legacy_input=False
            calls=0
            menus=[]
            def create(self,*args,**kwargs):
                self.calls+=1;self.menus.append([t['name'] for t in args[2]])
                tool,payload=('assess_item',raw) if self.calls==1 else ('stop',{'reason':'Controlled replay stops here; no new model judgment is invented'})
                return SimpleNamespace(content=[SimpleNamespace(type='tool_use',id='replay'+str(self.calls),name=tool,input=copy.deepcopy(payload))])
        client=Saved();client.menus=[]
        outcome=mod('p2').run_p2_agent(client,store,candidate,mod('agent').MeetingBudget(),mod('trace').Trace(),'TS3006b#10')
        branch.append({'version':name,'calls':client.calls,'menus':client.menus,
            'first_decision':outcome['history'][0]['decision'],'reason':outcome['history'][0]['reason'],
            'state':outcome['state'],'related_items':outcome['fields']['related_items']})
    assert branch[0]['first_decision']=='request_clarification' and branch[1]['first_decision']=='retrieve'
    assert 'get_prior_items' in branch[1]['menus'][1] and branch[1]['related_items']==[]
    result['same_first_submission_controller_comparison']=branch
    result['interpretation']='Same-input replay isolates wire/controller effects. Earlier full runs changed prompts and responses too; their difference cannot be attributed solely to random sampling.'
    save(ROOT/'replay.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='same_raw_submissions'},ensure_ascii=False))

if __name__=='__main__':main()
