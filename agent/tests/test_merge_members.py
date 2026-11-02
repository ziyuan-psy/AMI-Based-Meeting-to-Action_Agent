"""Malformed candidate pointers are rejected without guessing, losing peers or escaping budgets."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mta import config
from mta.control import Limits,RunControl,RunStopped
from mta.extract import _merge_groups,consolidate
from mta.llm import LLM
from mta.trace import StageTrace
from test_consolidate import cand

FIXTURE=Path(__file__).parent/'fixtures/consolidation_bad_members.json'

def candidates():return [cand('decisions','Use wood','m.A.1'),cand('decisions','Use a charger','m.A.2')]
def group(members):return {'type':'decisions','text':'Use wood','members':members}

@pytest.mark.parametrize('members',[[{'id':'MTD021PM'}],[0,{'id':'MTD021PM'}],[[0]],['0'],[True],[False],[0.0],[None],None,{},'0'])
def test_bad_member_types_reject_the_whole_group_without_touching_input(members):
    groups=[group(members)];original=copy.deepcopy(groups);trace=StageTrace()
    assert _merge_groups(groups,candidates(),trace,apply_cap=False)==[]
    assert groups==original
    assert trace.events[-1]['rejected'][0]['reason'] in ('members_must_be_array','members_must_be_candidate_indexes')

@pytest.mark.parametrize('groups',[None,{},'items',[None],[[]],['group'],[{'type':[],'text':'x','members':[0]}],
    [{'type':'decisions','text':{},'members':[0]}]])
def test_invalid_response_shapes_do_not_raise_or_become_facts(groups):
    trace=StageTrace();assert _merge_groups(groups,candidates(),trace,apply_cap=False)==[]
    assert trace.events[-1]['rejected']

def test_valid_group_survives_beside_an_invalid_group_and_keeps_only_observed_sources():
    groups=[group([{'id':'MTD021PM'}]),group([0,0,1])];trace=StageTrace()
    result=_merge_groups(groups,candidates(),trace,apply_cap=False)
    assert len(result)==1 and result[0]['candidate_indexes']==[0,1]
    assert result[0]['evidence_da_ids']==['m.A.1','m.A.2']
    assert trace.events[-1]['lineage'][0]['group_index']==1
    assert trace.events[-1]['rejected'][0]['group_index']==0

def test_valid_index_order_and_existing_range_filter_are_preserved():
    result=_merge_groups([group([-1,1,1,0,99])],candidates(),apply_cap=False)
    assert result[0]['candidate_indexes']==[1,0] and result[0]['evidence_da_ids']==['m.A.2','m.A.1']

@pytest.mark.parametrize('attempt,expected',[(17,0),(18,11),(19,0)])
def test_actual_original_responses_are_rejected_or_merged_without_reinterpreting_ids(attempt,expected):
    data=json.loads(FIXTURE.read_text(encoding='utf-8'))
    raw=next(r['raw_response'] for r in data['responses'] if r['attempt']==attempt);original=copy.deepcopy(raw)
    trace=StageTrace();result=_merge_groups(raw['items'],data['candidates'],trace,apply_cap=False)
    assert len(result)==expected and raw==original
    if attempt==17:
        assert [r['group_index'] for r in trace.events[-1]['rejected'] if r['reason']=='members_must_be_candidate_indexes']==[9,11]
    assert all(set(r['evidence_da_ids'])<={d for c in data['candidates'] for d in c['evidence_da_ids']} for r in result)

class Replies:
    def __init__(self,first_valid=False):self.calls=[];self.first_valid=first_valid;self.messages=NS(create=self.create)
    def create(self,**kw):
        self.calls.append(kw)
        good=self.first_valid and len(self.calls)==1
        raw={'items':[group([0] if good else [{'id':'P1'}])]}
        return NS(content=[NS(type='tool_use',name='submit_final_items',input=raw)],usage=NS(input_tokens=10,output_tokens=10))

def settings(monkeypatch,samples=3):
    monkeypatch.setattr(config,'WORKERS',1) # Deterministic unit scheduling; production settings are unchanged.
    monkeypatch.setattr(config,'CONSOLIDATE_SAMPLES',samples)
    monkeypatch.setattr(config,'ITEM_SELECTION_ENABLED',True)

def test_a_usable_sample_survives_other_samples_exhausting_the_original_three_attempts(monkeypatch):
    settings(monkeypatch);client=Replies(first_valid=True);trace=StageTrace()
    control=RunControl(Limits(8,1000000,5,20,1,0,0),trace)
    try:
        result=consolidate(LLM(client,model='fixture').scoped(control),candidates(),trace=trace)
        assert len(result)==1 and result[0]['candidate_indexes']==[0]
        assert len(client.calls)==7 and control.snapshot()['attempted_calls']==7 # 1 + 3 + 3; no new retries.
        attempts=[e for e in trace.events if e.get('event')=='attempt']
        assert [sum(e['sample']==i for e in attempts) for i in range(3)]==[1,3,3]
        assert 'Correction:' in client.calls[2]['messages'][0]['content']
        assert any(e.get('event')=='vote_skipped' and e['reason']=='one_usable_sample' for e in trace.events)
    finally:control.seal()

def test_all_bad_samples_fail_explicitly_instead_of_publishing_raw_candidates(monkeypatch):
    settings(monkeypatch,2);client=Replies();trace=StageTrace()
    with pytest.raises(RuntimeError,match='no usable items'):
        consolidate(LLM(client,model='fixture'),candidates(),trace=trace)
    assert len(client.calls)==6
    assert not any(e.get('event')=='restored_actions' for e in trace.events)

def test_member_retries_cannot_escape_shared_call_budget(monkeypatch):
    settings(monkeypatch,1);client=Replies();control=RunControl(Limits(2,1000000,5,20,1,0,0))
    try:
        with pytest.raises(RunStopped,match='call_budget_exhausted'):
            consolidate(LLM(client,model='fixture').scoped(control),candidates())
        assert control.snapshot()['attempted_calls']==len(client.calls)==2
    finally:control.seal()

def test_invalid_cached_group_is_revalidated_and_replaced_not_counted_as_a_sample(tmp_path,monkeypatch):
    settings(monkeypatch,1);cache=tmp_path/'consolidate.json'
    consolidate(LLM(Replies(True),model='fixture'),candidates(),cache=cache)
    saved=json.loads(cache.read_text(encoding='utf-8'))
    raw=next(e for e in saved['run_traces'][0][0]['events'] if e['event']=='raw_groups')
    raw['groups']=[group([{'id':'P1'}])]
    cache.write_text(json.dumps(saved),encoding='utf-8')
    client=Replies(True);trace=StageTrace()
    result=consolidate(LLM(client,model='fixture'),candidates(),cache=cache,trace=trace)
    assert result and len(client.calls)==1
    assert any(e.get('event')=='sample_rejected' and e['source']=='cache' for e in trace.events)
