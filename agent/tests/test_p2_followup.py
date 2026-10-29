"""Wire corrections preserve selections; missing auxiliary fields do not skip required history."""
import copy
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from jsonschema import Draft202012Validator
import pytest
from mta import config
from mta.assessment_protocol import normalize_submission, compact_tool
from mta.p2 import relation_tool
from test_p2 import MemoryStore,item,CUR,OLD,D1,P1
from test_p2_protocol import compact
from test_deadline_trace import run


@pytest.mark.parametrize('alias',['evidence_da_ids:','quotes'])
def test_selection_key_alias_retains_explicit_ids_and_raw_input(alias):
    store,candidate=MemoryStore(),item();raw=compact(store,candidate)
    raw['owner_check'][alias]=raw['owner_check'].pop('evidence_da_ids')
    original=copy.deepcopy(raw)
    outcome,model,budget=run(store,candidate,[('assess_item',raw)])
    assert outcome['state']=='accepted' and len(model.calls)==1 and budget.used==0
    assert outcome['checks']['owner']['quotes'][0]['da_id']==D1
    assert outcome['history'][0]['raw_tool_input']==original and raw==original
    assert any(c.startswith('selection_key_alias:') for c in outcome['history'][0]['protocol_normalizations'])


def test_conflicting_selection_alias_is_not_resolved_by_guessing():
    raw={'statement':{'result':'pass','reason':'Supported','evidence_da_ids':[D1],'quotes':[CUR+'.unread']}}
    converted,changes=normalize_submission(raw,'assess_item')
    assert converted==raw and changes==[]


@pytest.mark.parametrize('identifier',[CUR+'.unread','TS3006c.X.future','scenario:p1:0-10'])
def test_alias_does_not_bypass_source_or_time_scope_validation(identifier):
    store,candidate=MemoryStore(),item();raw=compact(store,candidate,'stop')
    raw['statement'].pop('evidence_da_ids');raw['statement']['quotes']=[identifier]
    outcome,_,_=run(store,candidate,[('assess_item',raw)])
    assert not outcome['recordable'] and outcome['checks']['statement']['result']=='uncertain'
    assert identifier not in str(outcome['fields']['reference'])


def test_empty_unscoped_documents_and_identical_root_selection_are_redundant_only():
    store,candidate=MemoryStore(),item();raw=compact(store,candidate)
    raw['documents']=[];raw['evidence_da_ids']=copy.deepcopy(raw['statement']['evidence_da_ids'])
    outcome,model,_=run(store,candidate,[('assess_item',raw)])
    assert outcome['state']=='accepted' and len(model.calls)==1
    assert outcome['history'][0]['submitted_assessment']['statement']['evidence_da_ids']==[D1]
    assert set(outcome['history'][0]['protocol_normalizations'])>={'empty_unscoped_documents','identical_root_statement_selection'}


def test_nonempty_unscoped_document_and_different_root_ids_are_not_silently_removed():
    store,candidate=MemoryStore(),item();raw=compact(store,candidate)
    raw.update(documents=[{'passage_id':'unread','result':'supports','reason':'Unscoped'}],evidence_da_ids=[CUR+'.unread'])
    converted,changes=normalize_submission(raw,'assess_item')
    assert converted==raw and not changes
    outcome,model,_=run(store,candidate,[('assess_item',raw),('assess_item',compact(store,candidate))])
    assert outcome['history'][0]['outcome']=='invalid_action' and len(model.calls)==2
    assert 'unread' not in str(outcome['fields']['reference'])


def test_nested_document_selections_are_preserved_for_normal_source_validation():
    raw={'statement':{'result':'pass','reason':'Context','quotes':[D1],
        'documents':[{'passage_id':'scenario:p1:0-10','result':'context','reason':'Read passage'}]},'documents':[]}
    converted,_=normalize_submission(raw,'assess_item')
    assert converted['statement']['documents']==raw['statement']['documents']
    assert converted['statement']['evidence_da_ids']==[D1] and 'documents' not in converted


@pytest.mark.parametrize('failure',['missing','empty'])
def test_missing_positive_proof_remains_a_schema_error(failure):
    store,candidate=MemoryStore(),item();raw=compact(store,candidate)
    if failure=='missing':raw['status_check'].pop('evidence_da_ids')
    else:raw['status_check']['evidence_da_ids']=[]
    converted,_=normalize_submission(raw,'assess_item')
    assert list(Draft202012Validator(compact_tool(True,'actions')['input_schema']).iter_errors(converted))


@pytest.mark.parametrize('result',['uncertain','fail'])
def test_unestablished_relation_can_omit_an_empty_links_array(result):
    raw={'result':result,'reason':'No relationship established','quotes':[],'next_action':'request_clarification'}
    converted,changes=normalize_submission(raw,'assess_relation')
    assert converted['links']==[] and converted['result']==result
    assert changes==['empty_links_for_unestablished_relation'] and 'links' not in raw
    assert Draft202012Validator(relation_tool()['input_schema']).is_valid(converted)


@pytest.mark.parametrize('raw',[
    {'result':'pass','reason':'Supported','quotes':[D1]},
    {'result':'uncertain','reason':'Proposed link','quotes':[],'related_items':[{'id':'TS3006a.sys.s.1','relation':'continues'}]},
])
def test_missing_link_evidence_is_never_created_for_a_positive_or_selected_relation(raw):
    converted,changes=normalize_submission(raw,'assess_relation')
    assert converted==raw and not changes and not Draft202012Validator(relation_tool()['input_schema']).is_valid(converted)


def historical_missing_deadline(store,candidate,hint='request_clarification'):
    candidate['flags']=['POSSIBLE_PRIOR']
    first=compact(store,candidate,hint)
    first.update(deadline=None,deadline_basis='missing')
    first['deadline_check'].update(result='uncertain',reason='No delivery deadline stated',evidence_da_ids=[])
    first['relation_check'].update(result='uncertain',reason='Need earlier context',evidence_da_ids=[])
    return first


def test_auxiliary_clarification_hint_gives_required_history_a_lookup_or_stop_choice():
    store,candidate=MemoryStore(),item();first=historical_missing_deadline(store,candidate)
    outcome,model,budget=run(store,candidate,[('assess_item',first),('stop',{'reason':'Cannot settle relation'})])
    assert len(model.calls)==2 and 'get_prior_items' in model.calls[1]['tools']
    assert 'request_clarification' in model.calls[1]['tools'] and 'stop' in model.calls[1]['tools']
    assert outcome['state']=='pending' and outcome['fields']['deadline'] is None and outcome['fields']['related_items']==[]
    assert budget.used==0 and outcome['history'][0]['decision']=='retrieve'
    import json
    context=json.loads(model.calls[1]['messages'][0]['content'])
    assert len(model.calls[1]['messages'])==1 and context['current_item']['text']==candidate['text']
    assert 'peer_items' not in context and 'provisional_values' not in context
    assert all(r['da_id'].startswith(CUR+'.') for r in context['current_dialogue'])


def test_explicit_stop_hint_is_not_overridden_to_force_a_history_lookup():
    store,candidate=MemoryStore(),item();first=historical_missing_deadline(store,candidate,'stop')
    outcome,model,budget=run(store,candidate,[('assess_item',first)])
    assert outcome['state']=='pending' and len(model.calls)==1 and budget.used==0


def test_required_history_opportunity_respects_the_original_lookup_budget(monkeypatch):
    monkeypatch.setattr(config,'MAX_CALLS_PER_MEETING',0)
    store,candidate=MemoryStore(),item();first=historical_missing_deadline(store,candidate)
    outcome,model,budget=run(store,candidate,[('assess_item',first)])
    assert outcome['state']=='pending' and len(model.calls)==1 and budget.used==0


def test_still_unsupported_history_after_lookup_can_stay_pending_without_a_link():
    store,candidate=MemoryStore(),item();first=historical_missing_deadline(store,candidate)
    store.get_prior_items=lambda *a,**k:[]
    outcome,model,budget=run(store,candidate,[('assess_item',first),('get_prior_items',{'query':'menu functions'}),
        ('assess_relation',{'result':'uncertain','reason':'Search does not establish a match','quotes':[],
                            'next_action':'request_clarification'})])
    assert len(model.calls)==3 and budget.used==1 and outcome['fields']['related_items']==[]
    assert outcome['checks']['relation']['result']=='uncertain' and outcome['recordable']


@pytest.mark.parametrize('tool',['assess_item','assess_relation','assess_document_context'])
def test_null_optional_documents_means_no_selected_passages_not_positive_support(tool):
    raw={'result':'uncertain','reason':'Not established','quotes':[],'documents':None,
         'statement':{'result':'pass','reason':'Current proof','evidence_da_ids':[D1],'documents':None}}
    converted,changes=normalize_submission(raw,tool)
    assert 'documents' not in converted and 'documents' not in converted['statement']
    assert converted['statement']['evidence_da_ids']==[D1] and converted['result']=='uncertain'
    assert set(changes)>={'no_document_selection:','no_document_selection:statement'}


def relation_selection(relation='continues',changed=()):
    return {'result':'pass','reason':'Compared read sources','quotes':[D1,P1],
        'links':[{'id':OLD+'.sys.s.1','relation':relation,'changed_fields':list(changed),
            'reason':'Compared exact property','prior_evidence_da_ids':[P1],'current_evidence_da_ids':[D1]}]}


@pytest.mark.parametrize('mutation',[
    lambda p:p['links'][0].update(id='link-'+OLD+'.sys.s.1'),
    lambda p:p['links'][0].update(prior_evidence_da_ids=['TS3006c.X.future']),
    lambda p:p['links'][0].update(current_evidence_da_ids=[CUR+'.unread']),
    lambda p:p.update(quotes=['The actual dialogue text, not its ID']),
])
def test_focused_relation_schema_only_offers_read_record_and_delivered_source_ids(mutation):
    schema=relation_tool([OLD+'.sys.s.1'],[D1],[P1])['input_schema']
    valid=relation_selection();assert Draft202012Validator(schema).is_valid(valid)
    bad=copy.deepcopy(valid);mutation(bad)
    assert not Draft202012Validator(schema).is_valid(bad)
    assert schema['type']=='object' and not set(schema)&{'allOf','anyOf','oneOf','not','enum','const'}


@pytest.mark.parametrize('relation,changed,valid',[
    ('continues',[],True),('continues',['scope'],False),
    ('updates',[],False),('updates',['scope'],True),('replaces',[],False),
])
def test_relationship_and_changed_fields_must_be_consistent_without_automatic_conversion(relation,changed,valid):
    schema=relation_tool([OLD+'.sys.s.1'],[D1],[P1])['input_schema']
    payload=relation_selection(relation,changed);original=copy.deepcopy(payload)
    assert Draft202012Validator(schema).is_valid(payload)==valid
    assert payload==original


def test_no_read_record_only_allows_an_empty_relation_and_does_not_mutate_global_schema():
    schema=relation_tool([], [D1], [])['input_schema']
    assert Draft202012Validator(schema).is_valid({'result':'uncertain','reason':'Not read','quotes':[],'links':[]})
    assert not Draft202012Validator(schema).is_valid(relation_selection())
    assert 'maxItems' not in relation_tool()['input_schema']['properties']['links']
    assert 'enum' not in relation_tool()['input_schema']['properties']['links']['items']['properties']['id']


def test_reworded_uncertainty_does_not_reset_the_semantic_no_progress_limit():
    store,candidate=MemoryStore(),item()
    first=compact(store,candidate,'retrieve')
    first['statement'].update(result='uncertain',reason='Need commitment evidence',evidence_da_ids=[])
    second=copy.deepcopy(first);second['statement']['reason']='No clear assignment has been established yet'
    third=copy.deepcopy(first);third['statement']['reason']='Additional context still does not establish commitment'
    outcome,model,budget=run(store,candidate,[('assess_item',first),
        ('search_dialogue',{'query':'menu assignment','scope':'current'}),('assess_item',second),
        ('search_dialogue',{'query':'menu responsibility','scope':'current'}),('assess_item',third)])
    assert outcome['termination_reason']=='no_progress' and not outcome['recordable']
    assert len(model.calls)==5 and budget.used==2
    checked=[e for e in outcome['history'] if e.get('outcome')=='checked']
    assert checked[-1]['semantic_stalls']==2 and checked[-1]['unresolved_before']==checked[-1]['unresolved_after']


def test_resolving_a_field_resets_stagnation_and_does_not_block_a_later_acceptance():
    store,candidate=MemoryStore(),item();first=compact(store,candidate,'retrieve')
    first['owner_check'].update(result='uncertain',reason='Need assignee',evidence_da_ids=[])
    first['status_check'].update(result='uncertain',reason='Need state',evidence_da_ids=[])
    second=compact(store,candidate,'retrieve')
    second['status_check'].update(result='uncertain',reason='State still not established',evidence_da_ids=[])
    outcome,model,_=run(store,candidate,[('assess_item',first),
        ('search_dialogue',{'query':'menu assignee','scope':'current'}),('assess_item',second),
        ('search_dialogue',{'query':'menu state','scope':'current'}),('assess_item',compact(store,candidate))])
    assert outcome['state']=='accepted' and len(model.calls)==5
    checked=[e for e in outcome['history'] if e.get('outcome')=='checked']
    assert checked[1]['semantic_stalls']==0 and checked[-1]['unresolved_after']==[]


def test_identical_relation_root_selection_is_redundant_without_discarding_quote_text():
    raw=relation_selection();raw['quotes']=[{'da_id':D1,'quote':'An invented quote remains invalid'},{'da_id':P1}]
    raw['evidence_da_ids']=[D1,P1];original=copy.deepcopy(raw)
    fixed,changes=normalize_submission(raw,'assess_relation')
    assert 'evidence_da_ids' not in fixed and fixed['quotes']==original['quotes'] and raw==original
    assert changes==['identical_root_relation_selection']


def test_different_relation_root_selection_is_not_silently_dropped():
    raw=relation_selection();raw['evidence_da_ids']=[CUR+'.unread']
    fixed,changes=normalize_submission(raw,'assess_relation')
    assert fixed==raw and not changes


def test_single_link_repeated_root_proof_can_be_removed_but_no_proof_is_created():
    raw=relation_selection()
    raw.update(prior_evidence_da_ids=[P1],current_evidence_da_ids=[D1]);original=copy.deepcopy(raw)
    fixed,changes=normalize_submission(raw,'assess_relation')
    assert fixed['links']==original['links'] and 'prior_evidence_da_ids' not in fixed and 'current_evidence_da_ids' not in fixed
    assert len(changes)==2 and raw==original
    original['links'][0].pop('prior_evidence_da_ids')
    fixed,_=normalize_submission(original,'assess_relation')
    assert fixed['prior_evidence_da_ids']==[P1] and 'prior_evidence_da_ids' not in fixed['links'][0]


def test_multiple_links_do_not_get_root_proof_assigned_to_an_arbitrary_record():
    raw=relation_selection();raw['links'].append(copy.deepcopy(raw['links'][0]))
    raw['prior_evidence_da_ids']=[P1]
    fixed,changes=normalize_submission(raw,'assess_relation')
    assert fixed==raw and not changes
