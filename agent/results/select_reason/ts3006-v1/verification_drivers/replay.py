"""Same actual inputs/responses under old and fixed selection code. Zero API."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
OLD=REPO/'agent/results/s2_dev16/787d88e-v1'
sys.path.insert(0,str(REPO/'agent/src'))
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    from mta import item_selection as current
    from mta.trace import StageTrace
    path=OLD/'source/agent/src/mta/item_selection.py'
    spec=importlib.util.spec_from_file_location('mta._selection_before',path)
    before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
    fixture=REPO/'agent/tests/fixtures/selection_missing_reason.json';data=load(fixture)
    assert sha(fixture)==load(ROOT/'capture.json')['fixture_sha256']
    rows=[];requests=[]
    class Captured:
        model=data['model']
        def __init__(self):self.calls=[]
        def forced_tool(self,system,user,name,schema,**kw):
            self.calls.append({'system':system,'input':json.loads(user),'name':name,'schema':schema,'options':kw})
            return copy.deepcopy(data['responses'][len(self.calls)-1]['raw_response'])
    for label,module in [('before',before),('after',current)]:
        client=Captured();trace=StageTrace(ROOT/(label+'.select.trace.jsonl'));error=None;output=None
        try:output=module.select_items(client,data['pool'],data['acts'],trace=trace,defer_action_limit=True)
        except ValueError as exc:error=str(exc)
        requests.append(client.calls)
        rows.append({'version':label,'captured_response_calls':len(client.calls),'error':error,'output':output})
    assert len(requests[0])==2 and len(requests[1])==1
    assert requests[0][0]==requests[1][0] and requests[1][0]['input']==data['input']
    assert rows[0]['error']=="item_selection_invalid: C10: 'reason' is a required property"
    assert rows[1]['error'] is None and rows[1]['output']
    canonical=[]
    for response in data['responses']:
        raw=copy.deepcopy(response['raw_response']);changes=[]
        result=current.canonical_reviews(raw,data['pool'],changes)
        assert raw==response['raw_response']
        assert current.selection_error(result,data['pool'],{d for i in data['pool'] for d in i['evidence_da_ids']}) is None
        canonical.append({'original_attempt':response['attempt'],'normalizations':changes,'result':result})
    save(ROOT/'replay.json',{'result':'PASSED','actual_new_api_calls':0,
        'scope':'Same actual keyed reviews, pool, enriched dialogue, model setting and first requested tool. Only code-derived duplicate metadata changes.',
        'fixture_sha256':sha(fixture),'before_source_sha256':sha(path),
        'after_source_sha256':sha(REPO/'agent/src/mta/item_selection.py'),
        'same_first_model_request':True,'full_selection':rows,'canonical_responses':canonical,
        'ordinary_missing_reason_or_unsafe_disposition_still_rejected':'See test_selection_metadata.py; no selection, owner, quote or evidence is filled.'})
    print(json.dumps({'result':'PASSED','actual_new_api_calls':0,'before_response_calls':2,
        'after_response_calls':1,'same_first_model_request':True,'both_canonical_responses_valid':True}))

if __name__=='__main__':main()
