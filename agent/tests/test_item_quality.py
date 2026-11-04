"""Boundary and captured-data regressions for retention, atomic selection and bounded revision."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mta import config
from mta.extract import restore_actions, consolidate
from mta.item_selection import select_items, selection_error, ranked_cap
from mta.statement_revision import deletion
from mta.trace import StageTrace
from test_consolidate import cand
from test_p2 import MemoryStore, item, execute, CUR, D1
from test_p2_protocol import compact


def test_same_evidence_decision_does_not_absorb_an_action():
    action = cand('actions', 'The UI will specify technical functions.', 'm.A.1', 'P1')
    decision = cand('decisions', 'The UI should specify technical functions.', 'm.A.1')
    result = restore_actions([decision], [action], apply_cap=False)
    assert [x['type'] for x in result] == ['decisions', 'actions']


def test_unsafe_duplicate_proposal_preserves_original_action_for_p2():
    from mta.item_selection import canonical_reviews
    pool=[cand('actions','UI will specify functions','m.A.1','P1'),cand('decisions','Specify functions','m.A.1')]
    raw={'C0':{'keep':False,'claims':[],'reason':'Same lines','duplicate_of':'C1'},
         'C1':{'keep':True,'claims':[{'text':'Specify functions','priority':2}],'reason':'Choice'}}
    changes=[];result=canonical_reviews(raw,pool,changes)
    assert any(x['type']=='actions' and x['text']==pool[0]['text'] for x in result['items'])
    assert changes[0]['action']=='preserved_original_for_p2'


def test_real_first_fixed_key_response_does_not_fail_on_milestone_duplicate():
    from mta.item_selection import canonical_reviews
    events=[json.loads(s) for s in (config.ROOT/'results/quality_repairs/retention-v3/after/s2/IS1008a.stages.jsonl').read_text(encoding='utf-8').splitlines()]
    attempt=next(e for e in events if e['stage']=='select' and e['event']=='attempt')
    pool=next(e for e in reversed(events) if e['stage']=='consolidate' and e['event']=='completed')['items']
    result=canonical_reviews(attempt['raw_response'],pool)
    assert any(e['index']==14 and e['category']=='duplicate' for e in result['excluded'])
    assert selection_error(result,pool,{d for x in pool for d in x['evidence_da_ids']}) is None


def test_fixed_keys_cannot_be_omitted_or_replaced_by_output_positions():
    from mta.item_selection import canonical_reviews
    pool=[cand('decisions','Keep LCD','m.A.1')]
    for raw in [{},{'0':{'keep':False,'claims':[],'reason':'Procedure'}}]:
        with pytest.raises(ValueError):canonical_reviews(raw,pool)


def test_same_line_different_actor_duplicate_is_preserved_for_independent_check():
    from mta.item_selection import canonical_reviews
    pool=[cand('actions','UI designs menu','m.A.1','P1'),cand('actions','ID designs case','m.A.1','P2')]
    raw={'C0':{'keep':False,'claims':[],'reason':'Same line','duplicate_of':'C1'},
         'C1':{'keep':True,'claims':[{'text':pool[1]['text'],'priority':3}],'reason':'Task'}}
    result=canonical_reviews(raw,pool)
    assert {tuple(x['members']) for x in result['items']}=={(0,),(1,)}


def test_kept_claim_with_duplicate_hint_does_not_abort_or_drop_source():
    from mta.item_selection import canonical_reviews
    pool=[cand('decisions','Keep LCD','m.A.1')]
    raw={'C0':{'keep':True,'claims':[{'text':'Keep LCD','priority':3}],'reason':'Supported','duplicate_of':'C0'}}
    changes=[];result=canonical_reviews(raw,pool,changes)
    assert result['items'][0]['text']=='Keep LCD' and changes[0]['action']=='ignored_duplicate_hint_on_kept_source'


def test_procedure_rule_excludes_introductions_but_preserves_product_introduction():
    from mta.item_selection import canonical_reviews
    pool=[cand('decisions','The activity is introduction and get acquainted.','m.A.1'),
          cand('decisions','Product introduction to customers will occur at launch.','m.A.2')]
    raw={f'C{i}':{'keep':True,'claims':[{'text':s['text'],'priority':3}],'reason':'Claim'} for i,s in enumerate(pool)}
    result=canonical_reviews(raw,pool)
    assert len(result['items'])==1 and result['items'][0]['members']==[1]
    assert result['excluded'][0]['category']=='procedure'


def test_action_limit_covers_explicit_roles_before_extra_tasks_for_same_owner(monkeypatch):
    monkeypatch.setattr(config,'MAX_ITEMS',{'actions':2,'decisions':7,'problems':4})
    pool=[{**cand('actions',str(i),'m.A.'+str(i),owner), 'selection_priority':2}
          for i,owner in enumerate(['P1','P1','P2'])]
    assert [x['text'] for x in ranked_cap(pool)]==['0','2']


def test_different_actor_on_same_lines_is_not_absorbed():
    a = cand('actions', 'The UI will specify technical functions.', 'm.A.1', 'P1')
    b = cand('actions', 'The ID will draw the case.', 'm.A.1', 'P2')
    assert len(restore_actions([b], [a], apply_cap=False)) == 2


def test_real_captured_ui_assignment_survives_decision_replacement(tmp_path,monkeypatch):
    import shutil
    import runpy
    from mta import extract
    historical=runpy.run_path(str(config.ROOT/'results/quality_repairs/release-v9/source/agent/src/mta/prompts.py'))
    monkeypatch.setattr(extract,'CONSOLIDATE_SYSTEM',historical['CONSOLIDATE_SYSTEM'])
    monkeypatch.setattr(extract,'DECISION_CONSOLIDATE_SYSTEM',historical['DECISION_CONSOLIDATE_SYSTEM'])
    root = config.ROOT / 'results/p2_repairs/repair-v3'
    captured = list((root/'after/s2/_attempts/TS3006a').glob('*/checkpoints/extracted.json'))
    assert len(captured)==1
    candidates = json.loads(captured[0].read_text(encoding='utf-8'))
    for suffix in ('', '.decisions'):
        shutil.copyfile(root/f'after/_cache/consolidate_TS3006a{suffix}.json', tmp_path/f'consolidate_TS3006a{suffix}.json')
    class NoCalls:
        model = json.loads((root/'study.json').read_text(encoding='utf-8'))['runtime']['model']['name']
        def forced_tool(self, *args, **kwargs):
            raise AssertionError('Captured consolidation must not spend API calls')
    out = consolidate(NoCalls(), candidates, tmp_path/'consolidate_TS3006a.json', enable_decision_pass=True)
    assert any(x['type']=='actions' and 'specify the technical functions' in x['text'] for x in out)


def test_final_limit_ranks_restored_core_task_above_low_priority_tail(monkeypatch):
    monkeypatch.setattr(config, 'MAX_ITEMS', {'actions':1,'decisions':7,'problems':4})
    weak = {**cand('actions','Discuss things','m.A.1'), 'selection_priority':1}
    recovered = {**cand('actions','PM will send the promised slides','m.A.2','P1'), 'selection_priority':3,'restored':True}
    trace = StageTrace()
    assert ranked_cap([weak,recovered],trace) == [recovered]
    assert trace.events[-1]['dropped'][0]['item'] == weak


def choice(text, members, evidence, typ='decisions'):
    return {'type':typ,'text':text,'members':members,'priority':3,'reason':'Independent project choice'}


def test_atomic_choices_can_share_one_candidate_and_real_evidence(tmp_path):
    pool = [cand('decisions','Price 25; sales target 50 million; costs half the price.','m.A.1')]
    response = {'C0':{'keep':True,'claims':[{'text':t,'priority':3} for t in ['Price is 25.','Sales target is 50 million.','Production costs are half the price.']], 'reason':'Independent choices'}}
    class Model:
        model='offline'
        def forced_tool(self,*args,**kwargs):return response
    acts=[{'id':'m.A.1','text':pool[0]['text'],'attributes':{'role':'PM','participant':'P1'}}]
    out=select_items(Model(),pool,acts,tmp_path/'selection.json')
    assert len(out)==3 and all(x['source_item_indexes']==[0] for x in out)


@pytest.mark.parametrize('mutate',[
    lambda r:r['items'][0].update(evidence_da_ids=['future.X.1']),  # No independent source namespace may be submitted.
    lambda r:r['items'][0].update(members=[8]),
    lambda r:r['items'][0].update(type='actions'),
    lambda r:r.update(items=[]),
    lambda r:r['excluded'].append({'index':0,'reason':'Duplicate','category':'duplicate'}),
])
def test_selection_cannot_invent_sources_actions_or_silent_dispositions(mutate):
    pool=[cand('decisions','Keep the screen','m.A.1')]
    result={'items':[choice('Keep the screen',[0],['m.A.1'])],'excluded':[]}
    mutate(result)
    assert selection_error(result,pool,['m.A.1'])


def test_all_excluded_is_a_valid_empty_project_record():
    pool=[cand('decisions','Get acquainted','m.A.1')]
    assert selection_error({'items':[],'excluded':[{'index':0,'reason':'Introductions, not project choice','category':'procedure'}]},pool,['m.A.1']) is None


def test_missing_empty_exclusion_is_normalized_only_when_every_candidate_is_covered():
    from mta.item_selection import normalize_submission
    pool=[cand('decisions','Keep LCD','m.A.1')]
    complete={'items':[choice('Keep LCD',[0],['m.A.1'])]}
    assert normalize_submission(complete,pool)['excluded']==[]
    incomplete=normalize_submission({'items':[]},pool)
    assert 'excluded' not in incomplete and selection_error(incomplete,pool,['m.A.1'])


@pytest.mark.parametrize('suffix', ['not after lunch', 'only for TV', 'unless approved', 'with no batteries', 'if approved', 'after approval'])
def test_revision_cannot_delete_polarity_or_conditions(suffix):
    with pytest.raises(ValueError):
        deletion('The PM will reorganize the materials '+suffix+'.',suffix,[D1],{D1:{}},CUR)


def test_revision_cannot_use_future_unread_or_document_evidence():
    for identifier in ['TS3006c.X.1','scenario:p1:0-10',CUR+'.X.unread']:
        with pytest.raises(ValueError):
            deletion('The PM will reorganize the materials after this meeting.','after this meeting',[identifier],{D1:{}},CUR)


def test_false_named_deadline_cannot_pass_and_routes_to_one_bounded_revision():
    store,it=MemoryStore(),item()
    it.update(text='The UI will develop the menu by Friday.',deadline='by Friday')
    repaired={**it,'text':'The UI will develop the menu.','deadline':'next meeting'}
    outcome,model,_,_=execute(store,it,[('assess_item',compact(store,it)),
        ('revise_statement',{'remove_suffix':'by Friday','reason':'No Friday anchor; task is explicit','evidence_da_ids':[D1]}),
        ('assess_item',compact(store,repaired))])
    assert outcome['recordable'] and outcome['item_text']==repaired['text'] and len(model.calls)==3
    assert outcome['history'][0]['checks']['statement']['reason'].startswith('unanchored_time_literal:')


def test_actual_named_deadline_still_passes():
    store,it=MemoryStore(),item()
    store.meeting(CUR).acts[0]['text']='The UI will develop the menu by Friday.'
    it.update(text='The UI will develop the menu by Friday.',deadline='by Friday')
    outcome,_,_,_=execute(store,it,[('assess_item',compact(store,it))])
    assert outcome['state']=='accepted'


def test_unanchored_iso_date_stays_pending_when_agent_stops():
    store,it=MemoryStore(),item();it.update(text='The UI will develop the menu by 2030-01-01.',deadline='2030-01-01')
    outcome,_,_,_=execute(store,it,[('assess_item',compact(store,it,'stop'))])
    assert not outcome['recordable'] and outcome['state']=='pending'


def test_one_focused_core_review_can_retain_task_with_missing_auxiliary_fields():
    from test_p2 import Script
    from mta.p2 import run_p2_agent
    from mta.agent import MeetingBudget
    from mta.trace import Trace
    store,it=MemoryStore(),item()
    first=compact(store,it,'request_clarification')
    first['statement'].update(result='uncertain',reason='No formal deadline or assignment ceremony')
    first['deadline_check'].update(result='uncertain',reason='Missing deadline')
    first['deadline']=None;first['deadline_basis']='missing'
    good=compact(store,it)
    focused={k:good[k] for k in ('statement','conflict_check','next_action')}
    model=Script([('assess_item',first),('assess_core',focused)]);model.p2_legacy_input=False
    outcome=run_p2_agent(model,store,it,MeetingBudget(),__import__('mta.trace',fromlist=['Trace']).Trace(),CUR+'#0')
    assert outcome['recordable'] and outcome['state']=='pending' and outcome['fields']['deadline'] is None
    assert len(model.calls)==2 and model.calls[-1]['tools']==['assess_core','request_clarification','stop']


def test_focused_core_review_can_stop_and_cannot_bypass_unread_evidence():
    from test_p2 import Script
    from mta.p2 import run_p2_agent
    from mta.agent import MeetingBudget
    from mta.trace import Trace
    store,it=MemoryStore(),item();first=compact(store,it,'request_clarification')
    first['statement'].update(result='uncertain',reason='Commitment unclear')
    model=Script([('assess_item',first),('stop',{'reason':'Still uncertain'})]);model.p2_legacy_input=False
    result=run_p2_agent(model,store,it,MeetingBudget(),Trace(),CUR+'#0')
    assert not result['recordable'] and result['state']=='pending'
    bad=compact(store,it,'stop');bad['statement']['evidence_da_ids']=[CUR+'.X.unread']
    model=Script([('assess_item',bad)]);model.p2_legacy_input=False
    result=run_p2_agent(model,store,it,MeetingBudget(),Trace(),CUR+'#0')
    assert len(model.calls)==1 and not result['recordable']


def revision_steps(store,it):
    payload=compact(store,it,'revise_statement')
    payload['statement'].update(result='uncertain',reason='Only the trailing time qualifier is unsupported')
    return [('assess_item',payload),('revise_statement',{'remove_suffix':'after this meeting','reason':'No timing stated','evidence_da_ids':[D1]})]


def test_deletion_requires_a_fresh_full_assessment_and_keeps_original_trace():
    store,it=MemoryStore(),item()
    it['text']='The UI will develop the menu after this meeting.'
    expected={**it,'text':'The UI will develop the menu.'}
    outcome,model,trace,_=execute(store,it,revision_steps(store,it)+[('assess_item',compact(store,expected))])
    assert outcome['state']=='accepted' and outcome['item_text']==expected['text']
    assert outcome['initial_context']['item']['text']==it['text'] and len(model.calls)==3
    revisions=[e for e in outcome['history'] if e['action']=='revise_statement']
    assert revisions[0]['before_text']==it['text'] and revisions[0]['after_text']==expected['text']
    assert outcome['history'][-2]['assessed_text']==expected['text']


def test_revision_followed_by_stop_never_publishes_using_old_passes():
    store,it=MemoryStore(),item();it['text']='The UI will develop the menu after this meeting.'
    outcome,_,_,_=execute(store,it,revision_steps(store,it)+[('assess_item',compact(store,it,'stop') | {'statement':{'result':'uncertain','reason':'Still not established','evidence_da_ids':[]}})])
    assert not outcome['recordable'] and outcome['state']=='pending'


def test_second_revision_request_remains_pending():
    store,it=MemoryStore(),item();it['text']='The UI will develop the menu after this meeting.'
    outcome,_,_,_=execute(store,it,revision_steps(store,it)+[('assess_item',revision_steps(store,it)[0][1])])
    assert outcome['termination_reason']=='statement_revision_limit' and not outcome['recordable']


def test_revised_sidecar_requires_checked_text_and_reassessment():
    from mta.p2 import validate_outputs
    from mta.pipeline import make_record
    store,it=MemoryStore(),item();it['text']='The UI will develop the menu after this meeting.'
    repaired={**it,'text':'The UI will develop the menu.'}
    outcome,_,_,_=execute(store,it,revision_steps(store,it)+[('assess_item',compact(store,repaired))])
    record=make_record(CUR,[{**repaired,'fields':outcome['fields']}],[])
    outcome['record_id']=record[0]['id']
    payload={'format_version':1,'run_id':'test','meeting_id':CUR,'enabled':True,'items':[outcome]}
    questions={'format_version':1,'run_id':'test','meeting_id':CUR,'requests':[]}
    assert validate_outputs(payload,questions,record,store)==[]
    outcome['history'][-2]['assessed_text']=it['text']
    assert any('without reassessment' in e for e in validate_outputs(payload,questions,record,store))


def test_selection_attempt_uses_the_existing_hard_budget():
    from mta.control import RunControl, Limits, RunStopped
    from mta.llm import LLM
    from test_observation import FixtureClient
    control=RunControl(Limits(0,1000000,1,5,1,0,0))
    llm=LLM(FixtureClient()).scoped(control)
    pool=[cand('decisions','Keep the LCD','m.A.1')]
    acts=[{'id':'m.A.1','text':'Keep the LCD','attributes':{'role':'PM','participant':'P1'}}]
    try:
        with pytest.raises(RunStopped,match='call_budget_exhausted'):
            select_items(llm,pool,acts)
        assert control.snapshot()['attempted_calls']==0
    finally:control.seal()
