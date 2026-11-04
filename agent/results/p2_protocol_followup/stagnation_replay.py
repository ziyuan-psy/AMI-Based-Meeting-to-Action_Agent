"""Replay the exact v17 stalled tool stream under the same nine-response allowance. No API."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from replay import package

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
CAPTURE=ROOT/'fixed-v3'
def load(p):return json.loads(p.read_text(encoding='utf-8'))

def main():
    old=next(i for i in load(CAPTURE/'run/s2/TS3006b.p2.json')['items'] if i['item_key']=='TS3006b#10')
    candidate=load(CAPTURE/'TS3006b.triaged.json')[10]
    candidate['peer_items']=old['initial_context']['peer_items']
    captured=[]
    for e in old['history']:
        if 'raw_tool_input' in e:captured.append((e['action'],e['raw_tool_input']))
        elif e.get('outcome')=='lookup':captured.append((e['action'],e['args']))
    rows=[]
    for name,source in [('v17',CAPTURE/'source'),('current',REPO)]:
        mod=package('stalled_'+name,source);cfg=mod('config')
        cfg.DATA=CAPTURE/'inputs';cfg.TRANSCRIPTS=cfg.DATA/'transcripts'
        cfg.PARTICIPANTS=cfg.DATA/'participants.json';cfg.SCENARIO=cfg.DATA/'scenario_text.txt'
        class Stream:
            p2_legacy_input=False
            consumed=0
            def create(self,*args,**kwargs):
                if self.consumed>=len(captured):
                    raise mod('p2_admission').ItemAllowanceExhausted('Same captured allowance exhausted')
                tool,payload=captured[self.consumed];self.consumed+=1
                return SimpleNamespace(content=[SimpleNamespace(type='tool_use',id='captured_'+str(self.consumed),name=tool,input=copy.deepcopy(payload))])
        client=Stream();trace=mod('trace').Trace()
        result=mod('p2').run_p2_agent(client,mod('store').Store('TS3006b',True,CAPTURE/'run/s2'),candidate,
            mod('agent').MeetingBudget(),trace,'TS3006b#10')
        rows.append({'version':name,'captured_responses_consumed':client.consumed,'outcome':result})
    assert rows[0]['captured_responses_consumed']==9 and rows[1]['captured_responses_consumed']==8
    assert rows[1]['outcome']['termination_reason']=='no_progress'
    assert rows[0]['outcome']['fields']['related_items']==rows[1]['outcome']['fields']['related_items']==[]
    result={'scope':'Exact saved tool arguments, same transcript/prior record and at most nine captured responses, no new source or model judgment.',
            'comparison':rows,'gold_scored':False}
    (ROOT/'stagnation_replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([{'version':r['version'],'responses':r['captured_responses_consumed'],
        'termination':r['outcome']['termination_reason']} for r in rows],ensure_ascii=False))

if __name__=='__main__':main()
