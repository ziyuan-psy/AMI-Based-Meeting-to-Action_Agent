"""Actual missing-reason responses and the boundary of code-derived duplicate metadata."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mta import config
from mta.item_selection import SYSTEM,canonical_reviews,select_items,selection_error
from mta.trace import StageTrace
from test_consolidate import cand

CAPTURE=Path(__file__).parent/'fixtures/selection_missing_reason.json'

class Captured:
    def __init__(self,data):self.data=data;self.model=data['model'];self.calls=[]
    def forced_tool(self,system,user,*args,**kwargs):
        self.calls.append(json.loads(user))
        return copy.deepcopy(self.data['responses'][len(self.calls)-1]['raw_response'])

@pytest.mark.parametrize('index',[0,1])
def test_actual_failed_response_passes_without_changing_model_selections(index):
    data=json.loads(CAPTURE.read_text(encoding='utf-8'));raw=data['responses'][index]['raw_response']
    original=copy.deepcopy(raw);changes=[]
    result=canonical_reviews(raw,data['pool'],changes)
    delivered={d for source in data['pool'] for d in source['evidence_da_ids']}
    assert selection_error(result,data['pool'],delivered) is None
    reasons=[c for c in changes if c['action']=='duplicate_reason_from_explicit_target']
    assert len(reasons)==[3,6][index] and all(c['reason_origin']=='code_metadata' for c in reasons)
    assert {c['source']:c['duplicate_of'] for c in reasons}=={
        key:row['duplicate_of'] for key,row in raw.items() if 'reason' not in row}
    assert raw==original

def test_actual_first_response_finishes_selection_in_one_call_and_preserves_raw_trace():
    data=json.loads(CAPTURE.read_text(encoding='utf-8'));model=Captured(data);trace=StageTrace()
    output=select_items(model,data['pool'],data['acts'],trace=trace,defer_action_limit=True)
    assert output and len(model.calls)==1
    assert model.calls[0]==data['input']
    event=next(e for e in trace.events if e['event']=='attempt')
    assert event['raw_response']==data['responses'][0]['raw_response']
    assert 'reason' not in event['raw_response']['C10'] and event['error'] is None
    assert all(set(i['evidence_da_ids'])<={d for p in data['pool'] for d in p['evidence_da_ids']} for i in output)

def pair():
    pool=[cand('actions','UI designs menu','m.A.1','P1'),cand('actions','UI designs menu','m.A.2','P1')]
    raw={'C0':{'keep':False,'duplicate_of':'C1'},'C1':{'keep':True,'claims':[{'text':pool[1]['text'],'priority':3}],'reason':'Task'}}
    return pool,raw

def test_missing_duplicate_reason_keeps_existing_merge_and_evidence_checks():
    pool,raw=pair();changes=[];result=canonical_reviews(raw,pool,changes)
    assert len(result['items'])==1 and result['items'][0]['members']==[1,0]
    assert selection_error(result,pool,{'m.A.1','m.A.2'}) is None
    assert selection_error(result,pool,{'m.A.2'})=='member_evidence_not_delivered'

@pytest.mark.parametrize('mismatch',['owner','type'])
def test_reason_metadata_does_not_override_unsafe_duplicate_preservation(mismatch):
    pool,raw=pair()
    if mismatch=='owner':pool[0]['owner_ids']=['P2']
    else:pool[1]['type']='decisions'
    result=canonical_reviews(raw,pool)
    assert {tuple(i['members']) for i in result['items']}=={(0,),(1,)}
    assert any(i['text']==pool[0]['text'] and i['type']=='actions' for i in result['items'])
    assert not any(e['index']==0 for e in result['excluded'])

@pytest.mark.parametrize('mutation',[
    lambda r:r['C0'].pop('duplicate_of'),
    lambda r:r['C0'].update(duplicate_of='C0'),
    lambda r:r['C0'].update(duplicate_of='C99'),
    lambda r:r['C1'].update(keep=False,claims=[]),
    lambda r:r['C1'].update(claims=[]),
    lambda r:r['C0'].update(keep=True,claims=[{'text':'New task','priority':3}]),
    lambda r:r['C0'].update(claims=[{'text':'Contradictory kept claim','priority':3}]),
    lambda r:r['C0'].update(reason=None),
    lambda r:r['C0'].update(reason=''),
    lambda r:r['C0'].update(reason=123),
])
def test_missing_reasons_are_not_general_schema_repair(mutation):
    pool,raw=pair();mutation(raw);changes=[]
    with pytest.raises(ValueError):canonical_reviews(raw,pool,changes)
    assert not any(c['action']=='duplicate_reason_from_explicit_target' for c in changes)

def test_provided_reason_is_preserved_and_unknown_fields_are_rejected():
    pool,raw=pair();raw['C0'].update(reason='The model provided this exact explanation',claims=[])
    original=copy.deepcopy(raw);changes=[];canonical_reviews(raw,pool,changes)
    assert raw==original and changes==[]
    raw['C0']['unchecked_override']=True
    with pytest.raises(ValueError):canonical_reviews(raw,pool)

def test_old_selection_cache_key_is_not_reused(tmp_path):
    data=json.loads(CAPTURE.read_text(encoding='utf-8'));trace=StageTrace()
    select_items(Captured(data),data['pool'],data['acts'],trace=trace,defer_action_limit=True)
    user=next(e for e in trace.events if e['event']=='attempt')['input']
    old_key=hashlib.sha256(json.dumps([SYSTEM,config.ITEM_POLICY_VERSION,data['model'],user],sort_keys=True).encode()).hexdigest()
    cache=tmp_path/'selection.json';cache.write_text(json.dumps({'key':old_key,'response':data['responses'][0]['raw_response']}),encoding='utf-8')
    model=Captured(data);trace=StageTrace()
    select_items(model,data['pool'],data['acts'],cache=cache,trace=trace,defer_action_limit=True)
    assert len(model.calls)==1 and next(e for e in trace.events if e['event']=='attempt')['source']=='model'
    assert json.loads(cache.read_text(encoding='utf-8'))['key']!=old_key
