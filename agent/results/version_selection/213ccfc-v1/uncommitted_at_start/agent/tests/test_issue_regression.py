"""Issue evidence regression: real vote replay and source/decision guards, no API/gold."""
import copy,json,sys
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mta import config
from mta.extract import restore_problems,vote_items
from mta.item_selection import ranked_cap
from mta.assessment_protocol import complete_inactive_metadata,compact_tool
from mta.p2 import run_p2_agent
from mta.agent import MeetingBudget
from mta.trace import Trace
from test_p2 import MemoryStore,Script,item,CUR,D1
from test_p2_protocol import compact

def problem():
    store=MemoryStore();store.meetings[CUR].acts[0]['text']='We cannot evaluate the design because the component cost is missing.'
    candidate=item('problems');candidate.update(text='Missing component cost prevents design evaluation.',status='open',owner_ids=[],deadline=None,deadline_basis='missing')
    return store,candidate

def live(store,candidate,steps):
    model=Script(steps);model.p2_legacy_input=False
    budget=MeetingBudget();outcome=run_p2_agent(model,store,candidate,budget,Trace(),CUR+'#0')
    return outcome,model,budget

def test_actual_vote_failure_recovers_only_original_source_hypotheses_for_review():
    fixture=json.loads((Path(__file__).parent/'fixtures/issue_vote_source_mismatch.json').read_text(encoding='utf-8'))
    candidates,runs=fixture['candidates'],fixture['runs'];original=copy.deepcopy(candidates)
    voted=vote_items(runs)
    assert not any(i['type']=='problems' for i in voted)
    reviewed=restore_problems(voted,candidates)
    recovered=[i for i in reviewed if i.get('restored_problem')]
    assert len(recovered)==sum(i['type']=='problems' for i in candidates)==14
    for i in recovered:
        source=candidates[i['candidate_indexes'][0]]
        assert i['text']==source['text'] and i['evidence_da_ids']==source['evidence_da_ids']
        assert 'fields' not in i and 'recordable' not in i
    assert candidates==original and reviewed[:len(voted)]==voted
    assert sum(i['type']=='actions' for i in reviewed)==sum(i['type']=='actions' for i in voted)
    assert len([i for i in ranked_cap(reviewed) if i['type']=='problems'])==config.MAX_ITEMS['problems']

def test_covered_problem_source_is_not_added_again_and_decision_evidence_does_not_cover_it():
    _,candidate=problem();candidate['candidate_indexes']=[0]
    assert restore_problems([candidate],[candidate])==[candidate]
    decision={**candidate,'type':'decisions'}
    result=restore_problems([decision],[candidate])
    assert len(result)==2 and result[1]['restored_problem']

@pytest.mark.parametrize('typ',['decisions','problems'])
def test_neutral_nonaction_owner_fields_are_wire_redundancy_not_failed_evidence(typ):
    store,candidate=problem();candidate['type']=typ
    if typ=='decisions':candidate['status']='adopted'
    raw=compact(store,candidate);raw.update(owner=None,owner_basis='missing',deadline=None,deadline_basis='missing')
    original=copy.deepcopy(raw)
    outcome,model,_=live(store,candidate,[('assess_item',raw)])
    assert outcome['state']=='accepted' and len(model.calls)==1
    assert outcome['history'][0]['raw_tool_input']==original and raw==original
    assert len([n for n in outcome['history'][0]['protocol_normalizations'] if n.startswith('neutral_nonaction_field:')])==4

@pytest.mark.parametrize('typ',['actions','problems'])
def test_nonempty_claims_are_not_silently_removed_by_nonaction_compatibility(typ):
    raw={'owner':['P2'],'owner_basis':'explicit','deadline':'Friday','deadline_basis':'explicit'}
    converted,changes=complete_inactive_metadata(raw,item_type=typ)
    assert converted==raw and changes==[]
    if typ=='problems':assert list(Draft202012Validator(compact_tool(False,typ)['input_schema']).iter_errors(converted))

def test_problem_core_review_is_independent_and_cannot_confirm_status_or_add_sources():
    store,candidate=problem();first=compact(store,candidate,'request_clarification')
    first['statement'].update(result='uncertain',reason='No owner or remedy')
    first['status_check'].update(result='uncertain',reason='End status not settled')
    good=compact(store,candidate)
    outcome,model,budget=live(store,candidate,[('assess_item',first),('assess_core',{'statement':good['statement'],'next_action':'request_clarification'})])
    assert outcome['recordable'] and outcome['state']=='pending' and outcome['fields']['status']=='unknown'
    assert budget.used==0 and len(model.calls)==2
    context=json.loads(model.calls[1]['messages'][0]['content'])
    assert len(model.calls[1]['messages'])==1 and context['current_item']['type']=='problems'
    assert context['observed_dialogue'][0]['da_id']==D1 and 'does not require' in context['instruction']
    assert any(e['action']=='problem_core_review_context' for e in outcome['history'])
    assert {r['field'] for r in outcome['clarifications']}=={'status'}

@pytest.mark.parametrize('verdict',['uncertain','fail'])
def test_problem_type_alone_never_promotes_a_failed_or_uncertain_statement(verdict):
    store,candidate=problem();first=compact(store,candidate,'request_clarification')
    first['statement'].update(result=verdict,reason='This exact impact is not supported')
    outcome,model,_=live(store,candidate,[('assess_item',first),('stop',{'reason':'Still unsupported'})])
    assert not outcome['recordable'] and outcome['state']=='pending' and len(model.calls)==2

def test_problem_recheck_still_rejects_unread_evidence():
    store,candidate=problem();first=compact(store,candidate,'request_clarification')
    first['statement'].update(result='uncertain',reason='Need to verify the concern')
    forged={'result':'pass','reason':'Trust an unseen line','evidence_da_ids':[CUR+'.unread']}
    outcome,_,_=live(store,candidate,[('assess_item',first),('assess_core',{'statement':forged,'next_action':'request_clarification'})])
    assert not outcome['recordable'] and outcome['checks']['statement']['reason'].startswith('invalid_proposed_fields:')
    assert 'unread' not in str(outcome['fields']['reference'])
    checked=next(e for e in outcome['history'] if e['action']=='assess_core' and e['outcome']=='checked')
    assert 'unobserved_or_inexact_quote' in checked['validation_codes']['statement']

def test_undelivered_document_selection_remains_blocked_not_a_type_override():
    store,candidate=problem();raw=compact(store,candidate,'request_clarification')
    raw['statement']['documents']=[{'passage_id':'scenario:unread','result':'context','reason':'Not delivered'}]
    outcome,model,_=live(store,candidate,[('assess_item',raw)])
    assert not outcome['recordable'] and len(model.calls)==1
    assert outcome['checks']['statement']['reason']=='document_passage_not_delivered'
