"""Regressions for the fresh 3b886f6 smoke failures; no provider or gold reads."""
import copy
from pathlib import Path
import sys
from jsonschema import Draft202012Validator
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mta import config
from mta.agent import MeetingBudget
from mta.item_selection import ranked_cap
from mta.p2 import run_p2_agent
from mta.statement_revision import revision_candidates,revision_tool
from mta.trace import Trace
from test_consolidate import cand
from test_p2 import MemoryStore,Script,item,CUR,OLD,D1,D4,P1
from test_p2_protocol import compact
from test_linking import link_payload


def live(store,it,steps):
    model=Script(steps);model.p2_legacy_input=False
    result=run_p2_agent(model,store,it,MeetingBudget(),Trace(),CUR+'#0')
    return result,model


def test_low_priority_explicit_owner_is_covered_before_extra_tasks_for_other_owners(monkeypatch):
    monkeypatch.setattr(config,'MAX_ITEMS',{'actions':3,'decisions':7,'problems':4})
    pool=[{**cand('actions',text,D1,owner),'selection_priority':priority}
          for text,owner,priority in [('Plan','ID',3),('Another plan','ID',3),('Design','UI',3),('Send promised materials','PM',1)]]
    kept=ranked_cap(pool)
    assert len(kept)==3 and {o for it in kept for o in it['owner_ids']}=={'ID','UI','PM'}
    assert any(it['text']=='Send promised materials' for it in kept)


def test_blank_owner_does_not_steal_a_coverage_slot(monkeypatch):
    monkeypatch.setattr(config,'MAX_ITEMS',{'actions':2,'decisions':7,'problems':4})
    pool=[{**cand('actions','Plan',D1,'ID'),'selection_priority':3},
          {**cand('actions','Another plan',D1,'ID'),'owner_ids':['ID',''],'selection_priority':3},
          {**cand('actions','Send materials',D1,'PM'),'selection_priority':1}]
    assert [i['text'] for i in ranked_cap(pool)]==['Plan','Send materials']


def test_final_cap_uses_checked_owner_instead_of_extractor_guess(monkeypatch):
    monkeypatch.setattr(config,'MAX_ITEMS',{'actions':2,'decisions':7,'problems':4})
    pool=[{**cand('actions','Design',D1,'UI'),'selection_priority':3,'fields':{'owner':['UI'],'owner_basis':'explicit'}},
          {**cand('actions','Send materials',D1,'UI'),'selection_priority':1,'fields':{'owner':['PM'],'owner_basis':'explicit'}},
          {**cand('actions','Copy mail',D1,'UI'),'selection_priority':2,'fields':{'owner':['UI'],'owner_basis':'explicit'}}]
    assert {i['text'] for i in ranked_cap(pool)}=={'Design','Send materials'}


def test_candidate_cleaning_discards_empty_and_null_participants():
    from mta.extract import _clean
    it=cand('actions','Send materials',D1,'PM');it['owner_ids']=['PM','',None,'  ','PM']
    assert _clean(it,[D1])['owner_ids']==['PM']


def test_explicit_exclusion_can_omit_empty_claims_without_dropping_other_sources():
    from mta.item_selection import canonical_reviews,selection_error
    pool=[cand('actions','Keep a task',D1,'P2'),cand('decisions','The same milestone',D1)]
    raw={'C0':{'keep':True,'claims':[{'text':'Keep a task','priority':3}],'reason':'Assignment'},
         'C1':{'keep':False,'duplicate_of':'C0','reason':'Milestone covered by task'}}
    changes=[];result=canonical_reviews(raw,pool,changes)
    assert selection_error(result,pool,[D1]) is None and len(result['items'])==1
    assert result['excluded'][0]['index']==1 and changes==[{'source':'C1','action':'empty_claims_for_explicit_exclusion'}]
    assert 'claims' not in raw['C1']


def test_kept_review_without_claims_is_not_filled_and_reports_its_source_key():
    from mta.item_selection import canonical_reviews
    pool=[cand('actions','Keep a task',D1,'P2')]
    with pytest.raises(ValueError,match='C0.*claims'):
        canonical_reviews({'C0':{'keep':True,'reason':'Incomplete'}},pool)


def test_publication_cap_keeps_omitted_valid_checks_in_checkpoint(tmp_path,monkeypatch):
    import json
    from mta import pipeline
    from test_observation import fixture_llm
    monkeypatch.setattr(config,'MAX_ITEMS',{'actions':1,'decisions':7,'problems':4})
    mid='TS3006a'
    candidates=[{**cand('actions',text,mid+'.A.dialog-act.vkaraisk.146','MTD022ID'),'selection_priority':priority}
                for text,priority in [('Produce a working design.',3),('Work on the design.',2)]]
    monkeypatch.setattr(pipeline,'extract_candidates',lambda *a,**k:copy.deepcopy(candidates))
    monkeypatch.setattr(pipeline,'consolidate',lambda *a,**k:copy.deepcopy(candidates))
    monkeypatch.setattr(pipeline,'select_items',lambda *a,**k:copy.deepcopy(candidates))
    result=pipeline.run_meeting(config.TRANSCRIPTS/(mid+'.txt'),'s1',fixture_llm(),tmp_path,tmp_path/'cache')
    assert result['status']=='COMPLETED',result
    record=json.loads(Path(result['output']).read_text(encoding='utf-8'))
    p2=json.loads(Path(result['p2_artifact']).read_text(encoding='utf-8'))
    meta=json.loads((tmp_path/'s1'/f'{mid}.run.json').read_text(encoding='utf-8'))
    omitted=json.loads(Path(meta['checkpoints']['verified_omitted']).read_text(encoding='utf-8'))['outcomes']
    assert len([r for r in record if r['type']=='actions'])==1 and len(p2['items'])==1
    assert len(omitted)==1 and omitted[0]['recordable'] and omitted[0]['checks']['statement']['result']=='pass'
    assert result['quarantined']==0 and result['verified omitted by limit']==1


def test_uncertain_conflict_can_be_rechecked_after_agent_selects_clarification():
    store,it=MemoryStore(),item();first=compact(store,it,'retrieve')
    first.update(deadline=None,deadline_basis='missing')
    first['deadline_check'].update(result='uncertain',reason='Deadline missing',evidence_da_ids=[])
    first['conflict_check'].update(result='uncertain',reason='No contradictions found, but limited information',affected_fields=['statement','deadline'])
    good=compact(store,it)
    focused={k:good[k] for k in ('statement','conflict_check','next_action')}
    result,model=live(store,it,[('assess_item',first),('request_clarification',{'reason':'Need a deadline'}),('assess_core',focused)])
    assert result['state']=='pending' and result['recordable'] and result['fields']['deadline'] is None
    assert model.calls[2]['tools']==['assess_core','request_clarification','stop']
    assert sum(e['action']=='core_recheck_offered' for e in result['history'])==1


def test_core_review_does_not_promote_a_real_conflict_or_override_agent_stop():
    store,it=MemoryStore(),item();first=compact(store,it,'retrieve')
    first['conflict_check'].update(result='fail',reason='Conflicting owner assignment',evidence_da_ids=[D1],affected_fields=['statement','owner'])
    result,model=live(store,it,[('assess_item',first),('stop',{'reason':'Contradiction remains'}),('stop',{'reason':'Still unresolved'})])
    assert not result['recordable'] and result['state']=='pending' and len(model.calls)==3


def test_core_recheck_cannot_bypass_unread_conflict_evidence():
    store,it=MemoryStore(),item();first=compact(store,it,'retrieve')
    first['conflict_check'].update(result='uncertain',evidence_da_ids=[CUR+'.X.unread'],affected_fields=['statement'])
    result,model=live(store,it,[('assess_item',first),('request_clarification',{'reason':'Evidence missing'})])
    assert not result['recordable'] and len(model.calls)==2


def test_focused_core_ignores_auxiliary_claims_without_changing_checked_fields():
    store,it=MemoryStore(),item();first=compact(store,it,'request_clarification')
    first['statement'].update(result='uncertain',reason='Assignment unclear')
    first.update(deadline=None,deadline_basis='missing')
    first['deadline_check'].update(result='uncertain',reason='Deadline missing',evidence_da_ids=[])
    good=compact(store,it)
    focused={k:good[k] for k in ('statement','conflict_check','next_action')}
    focused.update(owner=['P3'],deadline='Friday',deadline_check=good['deadline_check'],relation_check={'result':'pass','reason':'Invented extra'})
    result,_=live(store,it,[('assess_item',first),('assess_core',focused)])
    assert result['recordable'] and result['fields']['owner']==['P2'] and result['fields']['deadline'] is None
    normalization=result['history'][-2]['protocol_normalizations']
    assert 'ignored_out_of_scope_core_field:deadline' in normalization and 'ignored_out_of_scope_core_field:relation_check' in normalization


def test_revision_is_not_offered_without_a_safe_trailing_qualifier():
    store,it=MemoryStore(),item();it['text']='User requirements are not clearly defined; no specifics identified.'
    assert revision_candidates(it['text'])==[]
    first=compact(store,it,'retrieve');first['statement'].update(result='uncertain',evidence_da_ids=[],reason='Core uncertain')
    result,model=live(store,it,[('assess_item',first),('stop',{'reason':'No useful evidence'})])
    assert 'revise_statement' not in model.calls[1]['tools'] and not result['recordable']


def test_deadline_only_gap_does_not_offer_an_irrelevant_participant_lookup():
    store,it=MemoryStore(),item();first=compact(store,it,'retrieve')
    first.update(deadline=None,deadline_basis='missing');first['deadline_check'].update(result='uncertain',reason='Missing time',evidence_da_ids=[])
    result,model=live(store,it,[('assess_item',first),('request_clarification',{'reason':'No deadline evidence'})])
    assert result['recordable'] and 'get_participants' not in model.calls[1]['tools'] and 'read_dialogue' in model.calls[1]['tools']


def test_inactive_history_metadata_and_missing_action_hint_do_not_erase_supported_task():
    store,it=MemoryStore(),item();store.allow_prior=False
    store.meetings[CUR].acts[0]['text']='The UI will develop the menu.'
    first=compact(store,it);first.pop('next_action');first['relation_check']={'result':'not_applicable'}
    first.update(deadline=None,deadline_basis='missing');first['deadline_check']={'result':'uncertain'}
    result,model=live(store,it,[('assess_item',first)])
    assert result['recordable'] and result['state']=='pending' and result['fields']['deadline'] is None
    assert len(model.calls)==1 and result['checks']['relation']['reason']=='no_readable_prior_meetings'
    assert result['history'][0]['requested_action'] is None
    assert 'controller_action_for_missing_hint:request_clarification' in result['history'][0]['protocol_normalizations']


@pytest.mark.parametrize('hint',[None,' '])
def test_empty_optional_action_hint_is_conservative_and_does_not_add_facts(hint):
    store,it=MemoryStore(),item()
    store.meetings[CUR].acts[0]['text']='The UI will develop the menu.'
    first=compact(store,it);first['next_action']=hint
    first.update(deadline=None,deadline_basis='missing');first['deadline_check'].update(result='uncertain')
    result,_=live(store,it,[('assess_item',first)])
    assert result['recordable'] and result['state']=='pending' and result['fields']['deadline'] is None
    assert 'empty_optional_action_hint' in result['history'][0]['protocol_normalizations']


@pytest.mark.parametrize('relation',[None,{'result':'not_applicable','reason':' ','evidence_da_ids':None}])
def test_only_scope_known_inactive_history_is_completed(relation):
    store,it=MemoryStore(),item();store.allow_prior=False;first=compact(store,it)
    if relation is None:first.pop('relation_check')
    else:first['relation_check']=relation
    result,_=live(store,it,[('assess_item',first)])
    assert result['state']=='accepted' and result['checks']['relation']['quotes']==[]


def test_missing_history_metadata_in_a_required_check_is_not_filled_from_scope():
    from mta.assessment_protocol import complete_inactive_metadata,compact_tool
    store,it=MemoryStore(),item();first=compact(store,it);first['relation_check']={'result':'not_applicable'}
    completed,changes=complete_inactive_metadata(first,no_prior_scope=False)
    assert completed['relation_check']==first['relation_check'] and not changes
    assert list(Draft202012Validator(compact_tool(True,'actions',True)['input_schema']).iter_errors(completed))


def test_wire_metadata_completion_cannot_supply_positive_proof_or_clear_a_conflict():
    from mta.assessment_protocol import complete_inactive_metadata,compact_tool
    store,it=MemoryStore(),item();first=compact(store,it);first['statement']={'result':'pass'}
    first['conflict_check']={'result':'uncertain'}
    completed,_=complete_inactive_metadata(first,no_prior_scope=True)
    assert completed['statement']=={'result':'pass'}
    assert completed['conflict_check']['affected_fields']==['statement'] and completed['conflict_check']['evidence_da_ids']==[]
    assert list(Draft202012Validator(compact_tool(False,'actions')['input_schema']).iter_errors(completed))


def test_empty_revision_is_rejected_not_filled_and_valid_retry_requires_recheck():
    store,it=MemoryStore(),item();it.update(text='The UI will develop the menu by Friday.',deadline='by Friday')
    revised={**it,'text':'The UI will develop the menu.','deadline':'next meeting'}
    result,model=live(store,it,[('assess_item',compact(store,it)),
        ('revise_statement',{'remove_suffix':'','reason':'','evidence_da_ids':[]}),
        ('revise_statement',{'remove_suffix':'by Friday','reason':'Friday not in dialogue','evidence_da_ids':[D1]}),
        ('assess_item',compact(store,revised))])
    assert result['recordable'] and result['item_text']==revised['text'] and len(model.calls)==4
    assert result['history'][1]['outcome']=='invalid_action' and result['history'][1]['raw_tool_input']['evidence_da_ids']==[]
    assert sum(e.get('outcome')=='revised_requires_recheck' for e in result['history'])==1
    assert result['history'][-2]['assessed_text']==revised['text']


def test_revision_menu_cannot_delete_conditions_or_reference_unread_sources():
    text='The UI will develop the menu by Friday.';schema=revision_tool(text,[D1])['input_schema']
    for payload in [{'remove_suffix':'','reason':'','evidence_da_ids':[]},
                    {'remove_suffix':'by Friday','reason':'Unsupported','evidence_da_ids':[OLD+'.X.unread']}]:
        assert list(Draft202012Validator(schema).iter_errors(payload))
    assert revision_candidates('The PM will reorganize the materials after approval.')==[]
    assert revision_candidates('The PM will reorganize the materials only for TV.')==[]


def test_history_is_retrieved_and_read_before_accepting_a_required_relation():
    store,it=MemoryStore(),item('decisions');it.update(text='Cancel the earlier LCD decision.',evidence_da_ids=[D4],flags=['STATUS_CHECK'])
    first=compact(store,it);first['statement']['evidence_da_ids']=[D4];first['status_check']['evidence_da_ids']=[D4]
    first['relation_check'].update(result='uncertain',reason='Need historical comparison')
    first['conflict_check']['evidence_da_ids']=[D4]
    link,finding=link_payload(store)
    relation={'result':'pass','reason':finding['reason'],'quotes':[P1,D4],
              'related_items':[{'id':link['id'],'relation':link['relation']}],'links':[link],'next_action':'accept'}
    result,model=live(store,it,[('assess_item',first),('get_prior_items',{'query':'LCD'}),
        ('read_dialogue',{'meeting_id':OLD,'da_id':P1,'window':0}),('assess_relation',relation)])
    assert result['recordable'] and result['fields']['related_items']==relation['related_items']
    assert model.calls[2]['choice']=={'type':'any'} and model.calls[3]['choice']['name']=='assess_relation'
    assert result['history'][0]['reason']=='required_history_check_before_partial_accept'
    assert result['relation_evidence'][0]['prior_quotes'] and result['relation_evidence'][0]['current_quotes']


@pytest.mark.parametrize('incomplete',['missing_links','mismatched_selection'])
def test_read_history_submission_can_be_corrected_without_dropping_required_evidence(incomplete):
    store,it=MemoryStore(),item('decisions');it.update(text='Cancel the earlier LCD decision.',evidence_da_ids=[D4],flags=['STATUS_CHECK'])
    first=compact(store,it)
    for key in ('statement','status_check','conflict_check'):first[key]['evidence_da_ids']=[D4]
    first['relation_check'].update(result='uncertain',reason='Need history')
    link,finding=link_payload(store)
    valid={'result':'pass','reason':finding['reason'],'quotes':[P1,D4],'links':[link],'next_action':'accept'}
    bad=copy.deepcopy(valid)
    if incomplete=='missing_links':bad.pop('links')
    else:bad.update(links=[],related_items=[{'id':link['id'],'relation':link['relation']}])
    result,model=live(store,it,[('assess_item',first),('get_prior_items',{'query':'LCD'}),
        ('read_dialogue',{'meeting_id':OLD,'da_id':P1,'window':0}),('assess_relation',bad),('assess_relation',valid)])
    assert result['recordable'] and result['fields']['related_items']==[{'id':link['id'],'relation':'cancels'}]
    assert len(model.calls)==5 and result['relation_evidence'][0]['state']=='supported'
    if incomplete=='missing_links':assert any(e.get('outcome')=='invalid_action' and 'links' in e['error'] for e in result['history'])
    else:assert any(e.get('reason')=='incomplete_link_evidence_submission' for e in result['history'])


def test_agent_can_stop_without_inventing_a_link_after_history_is_offered():
    store,it=MemoryStore(),item('decisions');it['flags']=['POSSIBLE_PRIOR']
    first=compact(store,it);first['relation_check'].update(result='uncertain',reason='Need historical comparison')
    result,model=live(store,it,[('assess_item',first),('stop',{'reason':'No reliable match'})])
    assert result['recordable'] and result['state']=='pending' and result['fields']['related_items']==[]
    assert len(model.calls)==2


@pytest.mark.parametrize('identifier',[OLD,CUR+'#0',OLD+'.sys.s.2'])
def test_meeting_id_only_query_is_rejected_before_consuming_a_lookup(identifier):
    store,it=MemoryStore(),item('decisions');it['flags']=['POSSIBLE_PRIOR']
    first=compact(store,it);first['relation_check'].update(result='uncertain',reason='Need historical comparison')
    result,model=live(store,it,[('assess_item',first),('get_prior_items',{'query':identifier}),('stop',{'reason':'Need content keywords'})])
    assert result['lookups']==0 and result['fields']['related_items']==[]
    rejected=next(e for e in result['history'] if e['outcome']=='invalid_action')
    assert 'record TEXT' in rejected['error']
