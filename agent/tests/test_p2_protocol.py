"""Real regression failure modes: selected IDs, partial fields, fair attempts and reserved completion."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace as NS

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'src'))

import pytest
from jsonschema import Draft202012Validator

from mta.assessment_protocol import compact_tool, canonical_assessment, CHECK_KEYS
from mta.p2 import run_p2_agent, provisional
from mta.agent import MeetingBudget
from mta.trace import Trace
from mta.control import Limits, RunControl, RunStopped
from mta.llm import LLM
from mta.p2_admission import P2Admission, ItemAllowanceExhausted
from test_p2 import MemoryStore, item, assessment, execute, Script, CUR, D1


def compact(store, it, next_action='accept'):
    old = assessment(store,it,next_action)
    fields = old['fields']
    result = {k:copy.deepcopy(fields[k]) for k in ('status','related_items','owner','owner_basis','deadline','deadline_basis')
              if it['type']=='actions' or k in ('status','related_items')}
    result['next_action'] = next_action
    for name,key in CHECK_KEYS.items():
        if name in ('owner','deadline') and it['type']!='actions':
            continue
        finding = old['checks'][name]
        result[key] = {'result':finding['result'],'reason':finding['reason'],
                       'evidence_da_ids':[q['da_id'] for q in finding['quotes']]}
        if name=='conflict':
            result[key]['affected_fields']=[]
    return result


@pytest.mark.parametrize('typ',['actions','decisions','problems'])
def test_flat_protocol_accepts_explicit_delivered_ids_without_copying_quotation_text(typ):
    store,it = MemoryStore(),item(typ)
    if typ=='problems':it['status']='open'
    payload=compact(store,it)
    schema=compact_tool(False,typ)['input_schema']
    assert not list(Draft202012Validator(schema).iter_errors(payload))
    assert not {'allOf','anyOf','oneOf'}.intersection(schema)
    outcome,model,_,budget=execute(store,it,[('assess_item',payload)])
    assert outcome['state']=='accepted' and budget.used==0 and len(model.calls)==1
    assert outcome['checks']['statement']['quotes'][0]['quote']==store.meeting(CUR).acts[0]['text']


@pytest.mark.parametrize('mutate',[
    lambda p:p['statement'].pop('evidence_da_ids'),
    lambda p:p['statement'].update(evidence_da_ids=[]),
    lambda p:p['status_check'].update(result='open'),
    lambda p:p.update(checks={'documents':[]}),
])
def test_flat_protocol_rejects_observed_live_mistakes_and_repairs_with_exact_schema_feedback(mutate):
    store,it=MemoryStore(),item()
    bad=compact(store,it); mutate(bad)
    outcome,model,trace,_=execute(store,it,[('assess_item',bad),('assess_item',compact(store,it))])
    assert outcome['state']=='accepted' and len(model.calls)==2
    assert trace.events[0]['outcome']=='invalid_action'


@pytest.mark.parametrize('identifier',['TS3006c.X.fake','scenario:p66:0-50',CUR+'.X.unread'])
def test_flat_pass_cannot_publish_an_unread_future_or_document_id_as_dialogue(identifier):
    store,it=MemoryStore(),item()
    payload=compact(store,it,'stop');payload['statement']['evidence_da_ids']=[identifier]
    outcome,_,_,_=execute(store,it,[('assess_item',payload)])
    assert not outcome['recordable'] and outcome['checks']['statement']['result']=='uncertain'


def test_unknown_auxiliary_fields_do_not_poison_a_supported_task_or_trigger_default_lookup():
    store,it=MemoryStore(),item();it['text']='Develop the menu'
    payload=compact(store,it)
    payload.update(owner=['INVALID'],owner_basis='explicit',deadline=None,deadline_basis='missing')
    for field in ('owner_check','deadline_check'):
        payload[field].update(result='uncertain',reason='Available dialogue does not settle this field',evidence_da_ids=[])
    outcome,model,_,budget=execute(store,it,[('assess_item',payload)])
    assert outcome['state']=='pending' and outcome['recordable'] and len(model.calls)==1 and budget.used==0
    assert outcome['fields']['owner'] is None and outcome['fields']['deadline'] is None
    assert set(outcome['unresolved_fields'])=={'owner','deadline'}


def test_owner_only_conflict_does_not_discard_the_separately_supported_task():
    store,it=MemoryStore(),item();it['text']='Develop the menu'
    payload=compact(store,it)
    payload['conflict_check'].update(result='uncertain',reason='Conflicting assignment',affected_fields=['owner'])
    outcome,_,_,_=execute(store,it,[('assess_item',payload)])
    assert outcome['recordable'] and outcome['state']=='pending' and outcome['fields']['owner'] is None


def test_auxiliary_uncertainty_does_not_hide_a_fabricated_statement_quote_in_legacy_protocol():
    store,it=MemoryStore(),item()
    payload=assessment(store,it,'stop')
    payload['checks']['statement']['quotes'][0]['quote']='Invented unsupported commitment'
    payload['checks']['owner'].update(result='uncertain',quotes=[])
    outcome,_,_,_=execute(store,it,[('assess_item',payload)])
    assert not outcome['recordable'] and outcome['checks']['statement']['reason']=='unobserved_or_inexact_quote'


def test_live_protocol_rejects_legacy_wrapper_instead_of_silently_allowing_missing_selections():
    store,it=MemoryStore(),item()
    old=assessment(store,it);old['checks']['statement'].pop('quotes')
    client=Script([('assess_item',old),('assess_item',compact(store,it))]);client.p2_legacy_input=False
    result=run_p2_agent(client,store,it,MeetingBudget(),Trace(),CUR+'#0')
    assert result['state']=='accepted' and result['history'][0]['outcome']=='invalid_action'
    assert 'fields' not in result['initial_context'] and result['initial_context']['candidate_evidence_da_ids']


def limits(calls=8,retries=0):
    return Limits(calls,1000000,2,10,4,retries,0)


def test_admission_reserves_every_waiting_first_attempt_and_the_completion_calls():
    parent=RunControl(limits())
    admission=P2Admission(parent,['a','b','c'],3)
    assert admission.capacity==5
    assert [admission.take('a') for _ in range(4)]==[True,True,True,False]
    assert admission.take('b') and admission.take('c') and not admission.take('a')
    assert admission.snapshot()['issued']==5 and parent.snapshot()['failure'] is None


def test_completed_items_release_unused_first_slots_to_other_agents():
    parent=RunControl(limits(calls=5)); admission=P2Admission(parent,['a','b'],3)
    assert admission.take('a') and not admission.take('a')
    admission.done('b')
    assert admission.take('a')


class Client:
    def __init__(self, function):
        self.calls=0;self.function=function;self.messages=NS(create=self.create)
    def create(self,**kw):
        self.calls+=1
        return self.function(self.calls,kw)


def response(name='submit',payload=None):
    return NS(content=[NS(type='tool_use',id='response',name=name,input=payload or {'ok':True})],
              usage=NS(input_tokens=10,output_tokens=5))


def test_actual_transport_retries_share_fair_admission_and_global_accounting():
    def transport(n,kw):
        if n==1:raise ConnectionError('transient')
        return response()
    client=Client(transport); model=LLM(client);parent=RunControl(limits(calls=6,retries=2))
    admission=P2Admission(parent,['a','b'],3)
    model.scoped(admission.view('a')).create('s',[])
    model.scoped(admission.view('b')).create('s',[])
    assert admission.snapshot()['attempts_by_item']=={'a':2,'b':1}
    assert client.calls==parent.snapshot()['attempted_calls']==3
    assert parent.snapshot()['unknown_usage_tokens']>0
    parent.seal()


def test_denied_transport_retry_remains_api_failure_not_pending_fact_validation():
    def broken(n,kw):raise ConnectionError('transient')
    client=Client(broken);parent=RunControl(limits(calls=5,retries=2))
    admission=P2Admission(parent,['a','b'],3)
    with pytest.raises(RunStopped,match='model_request_failed'):
        LLM(client).scoped(admission.view('a')).create('s',[])
    assert parent.snapshot()['failure']['allowance_exhausted'] and client.calls==1
    parent.seal()


def test_soft_item_stop_saves_supported_result_and_allows_summary_in_reserved_calls():
    store,it=MemoryStore(),item()
    payload=compact(store,it,'retrieve');payload['deadline_check'].update(result='uncertain',reason='Need original deadline',evidence_da_ids=[])
    payload.update(deadline=None,deadline_basis='missing')
    def transport(n,kw):
        if n==1:return response('assess_item',payload)
        if n==2:return response('get_participants',{'meeting_id':CUR})
        return response('submit_abstracts',{'abstracts':[]})
    client=Client(transport);parent=RunControl(limits(calls=4));admission=P2Admission(parent,['a'],2)
    model=LLM(client)
    outcome=run_p2_agent(model.scoped(admission.view('a')),store,it,MeetingBudget(),Trace(),CUR+'#0')
    assert outcome['recordable'] and outcome['termination_reason']=='p2_call_allowance_exhausted'
    assert outcome['state']=='pending' and parent.snapshot()['failure'] is None
    model.scoped(parent).forced_tool('summary','u','submit_abstracts',{'type':'object'})
    assert client.calls==3 and parent.snapshot()['attempted_calls']==3
    parent.seal()


def test_cached_upstream_budget_is_reserved_once_without_fabricating_new_provider_calls():
    from mta.p2_repair import RemainingBudgetLLM
    client=Client(lambda n,kw:response())
    parent=RunControl(limits(calls=8))
    model=RemainingBudgetLLM(Trace(),{'attempts':3,'charged_tokens':1000,'elapsed_seconds':1},client=client,model='offline')
    model.scoped(parent)
    assert parent.limits.calls==5 and parent.limits.tokens==999000
    assert parent.snapshot()['attempted_calls']==0
    admission=P2Admission(parent,['a'],3)
    model.scoped(admission.view('a')).create('s',[])
    assert parent.limits.calls==5 and client.calls==1 and model.calls==1
    assert parent.snapshot()['attempted_calls']==1 and admission.capacity==2
    parent.seal()


def test_budget_failed_before_run_uses_native_normalized_checkpoint_even_after_repository_move(tmp_path):
    import json
    from mta.p2_repair import before_normalized_path
    saved_parent=tmp_path/'old-location/current/s2'
    suffix=Path('_attempts/TS3006b/old-run/checkpoints/normalized.json')
    current_parent=tmp_path/'new-location/current/s2';current_parent.mkdir(parents=True)
    checkpoint=current_parent/suffix;checkpoint.parent.mkdir(parents=True);checkpoint.write_text('[]')
    (current_parent/'TS3006b.run.json').write_text(json.dumps({'status':'BUDGET_EXCEEDED',
        'artifacts':{'metadata':str(saved_parent/'TS3006b.run.json')},
        'checkpoints':{'normalized':str(saved_parent/suffix)}}))
    assert before_normalized_path(tmp_path/'new-location','TS3006b')==checkpoint


def test_redundant_no_conflict_fields_and_advisory_confidence_cannot_discard_valid_selected_evidence():
    store,it=MemoryStore(),item('decisions')
    payload=compact(store,it);payload['confidence']=0.9;payload['conflict_check'].pop('affected_fields')
    outcome,model,_,_=execute(store,it,[('assess_item',payload)])
    assert outcome['state']=='accepted' and len(model.calls)==1
    normalized=outcome['history'][0]['protocol_normalizations']
    assert 'ignored_advisory_confidence' in normalized and 'empty_affected_fields_for_no_conflict' in normalized


def test_missing_affected_fields_never_turns_an_uncertain_conflict_into_a_pass():
    store,it=MemoryStore(),item()
    payload=compact(store,it,'stop');payload['conflict_check'].update(result='uncertain',reason='Conflicting statements')
    payload['conflict_check'].pop('affected_fields')
    client=Script([('assess_item',payload),('stop',{'reason':'Cannot resolve conflict'})])
    outcome=run_p2_agent(client,store,it,MeetingBudget(),Trace(),CUR+'#0')
    assert not outcome['recordable'] and outcome['state']=='pending'


def test_current_dialogue_remains_available_for_owner_gap_with_unresolved_history():
    store,it=MemoryStore(),item();it['flags']=['POSSIBLE_PRIOR']
    first=compact(store,it,'retrieve')
    first['owner_check'].update(result='uncertain',reason='Need current assignment',evidence_da_ids=[])
    first['relation_check'].update(result='uncertain',reason='Historical comparison also needed')
    final=copy.deepcopy(first);final['next_action']='stop'
    client=Script([('assess_item',first),('read_dialogue',{'da_id':D1}),('assess_item',final)])
    outcome=run_p2_agent(client,store,it,MeetingBudget(),Trace(),CUR+'#0')
    lookups=[e for e in outcome['history'] if e.get('outcome')=='lookup']
    assert lookups and lookups[0]['args']['meeting_id']==CUR
    assert not any(e['outcome']=='invalid_action' for e in outcome['history'])
