"""Regressions for lost relative timing and unproductive saved TS3006b action paths."""
import copy
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mta.agent import MeetingBudget
from mta.p2 import run_p2_agent
from mta.p2_focus import has_content_query
from mta.trace import Trace
from test_p2 import MemoryStore, Script, item, CUR, D1, D2
from test_p2_protocol import compact


class LiveScript(Script):
    p2_legacy_input = False

    def __init__(self, steps):
        super().__init__(steps)
        self.menus = []

    def create(self, *args, **kwargs):
        self.menus.append(copy.deepcopy(args[2]))
        return super().create(*args, **kwargs)


def run(store, candidate, steps):
    model, budget = LiveScript(steps), MeetingBudget()
    outcome = run_p2_agent(model, store, candidate, budget, Trace(), CUR+'#0')
    return outcome, model, budget


def missing_time(store, candidate, hint=None):
    first = compact(store, candidate)
    first.update(deadline=None, deadline_basis='missing')
    first['deadline_check'].update(result='uncertain', reason='No deadline found', evidence_da_ids=[])
    if hint is None:
        first.pop('next_action')
    else:
        first['next_action'] = hint
    return first


def deadline(value, result='pass', ids=None):
    return {'deadline': value, 'deadline_basis': 'explicit' if value else 'missing',
            'deadline_check': {'result': result, 'reason': 'Review request and acceptance together',
                               'evidence_da_ids': [D1] if ids is None else ids},
            'next_action': 'accept' if result == 'pass' else 'request_clarification'}


def test_relative_delivery_timing_can_be_recovered_without_changing_other_fields():
    store, candidate = MemoryStore(), item()
    candidate.update(deadline=None, deadline_basis='missing')
    store.meetings[CUR].acts[0]['text'] = 'At the conclusion of our meeting, could the UI send the slides?'
    outcome, model, budget = run(store, candidate, [
        ('assess_item', missing_time(store, candidate)),
        ('assess_deadline', deadline('at the conclusion of our meeting'))])
    assert outcome['state'] == 'accepted' and outcome['fields']['deadline'] == 'at the conclusion of our meeting'
    assert outcome['fields']['owner'] == ['P2'] and outcome['item_text'] == candidate['text']
    assert budget.used == 0 and len(model.calls) == 2
    assert sum(e['action'] == 'deadline_recheck_offered' for e in outcome['history']) == 1
    assert model.calls[1]['tools'] == ['assess_deadline', 'request_clarification', 'stop']


@pytest.mark.parametrize('ending', ['uncertain', 'stop'])
def test_time_cue_never_assigns_a_deadline_and_review_can_decline(ending):
    store, candidate = MemoryStore(), item()
    step = ('assess_deadline', deadline(None, 'uncertain', [])) if ending == 'uncertain' else ('stop', {'reason':'Not agreed'})
    outcome, model, _ = run(store, candidate, [('assess_item', missing_time(store, candidate)), step])
    assert outcome['recordable'] and outcome['state'] == 'pending' and outcome['fields']['deadline'] is None
    assert len(model.calls) == 2


def test_deadline_review_keeps_named_time_source_validation():
    store, candidate = MemoryStore(), item()
    review = deadline('Friday'); review['next_action'] = 'request_clarification'
    outcome, _, _ = run(store, candidate, [('assess_item', missing_time(store, candidate)), ('assess_deadline', review)])
    assert outcome['fields']['deadline'] is None
    assert outcome['checks']['deadline']['reason'].startswith('unanchored_time_literal:')


def test_explicit_stop_hint_does_not_force_a_deadline_review():
    store, candidate = MemoryStore(), item()
    outcome, model, _ = run(store, candidate, [('assess_item', missing_time(store, candidate, 'stop'))])
    assert outcome['state'] == 'pending' and len(model.calls) == 1
    assert not any(e['action'] == 'deadline_recheck_offered' for e in outcome['history'])


def test_response_only_selection_can_revisit_timing_in_original_delivered_request():
    store, candidate=MemoryStore(),item();candidate['evidence_da_ids']=[D1,D2]
    store.meetings[CUR].acts[0]['text']='Yes, I will.'
    store.meetings[CUR].acts[1]['text']='Could you send the slides at the conclusion of our meeting?'
    first=missing_time(store,candidate,'request_clarification')
    review=deadline('at the conclusion of our meeting',ids=[D2])
    outcome, model, _=run(store,candidate,[('assess_item',first),('assess_deadline',review)])
    assert outcome['state']=='accepted' and outcome['fields']['deadline']=='at the conclusion of our meeting'
    assert len(model.calls)==2 and outcome['history'][1]['evidence'][0]['da_id']==D2
    context=json.loads(model.calls[1]['messages'][0]['content'])
    assert len(model.calls[1]['messages'])==1 and 'No deadline found' not in json.dumps(context)
    assert {r['da_id'] for r in context['candidate_dialogue']}=={D1,D2}


def test_terminal_clarification_tool_remains_available_with_a_time_cue():
    store,candidate=MemoryStore(),item()
    first=missing_time(store,candidate,'retrieve')
    outcome,model,_=run(store,candidate,[('assess_item',first),('request_clarification',{'reason':'No useful lookup'})])
    assert outcome['state']=='pending' and outcome['fields']['deadline'] is None and len(model.calls)==2


def test_nearby_unselected_time_does_not_trigger_a_review():
    store, candidate = MemoryStore(), item()
    store.meetings[CUR].acts[0]['text'] = 'The UI will develop the menu.'
    outcome, model, _ = run(store, candidate, [('assess_item', missing_time(store, candidate))])
    assert outcome['recordable'] and outcome['fields']['deadline'] is None and len(model.calls) == 1


def test_deadline_recheck_cannot_change_owner_or_use_unread_sources():
    store, candidate = MemoryStore(), item()
    wrong = deadline('next meeting'); wrong['owner'] = ['P3']
    unread = deadline('next meeting', ids=[CUR+'.unread']); unread['next_action'] = 'request_clarification'
    outcome, model, _ = run(store, candidate, [('assess_item', missing_time(store, candidate)),
        ('assess_deadline', wrong), ('assess_deadline', unread)])
    assert outcome['fields']['owner'] == ['P2'] and outcome['fields']['deadline'] is None
    assert 'unread' not in json.dumps(outcome['fields']['reference'])
    assert len(model.calls) == 3 and any(e.get('outcome') == 'invalid_action' for e in outcome['history'])


def test_repeated_keyword_search_is_not_executed_or_charged_a_second_lookup():
    store, candidate = MemoryStore(), item()
    first = compact(store, candidate, 'retrieve')
    first['statement'].update(result='uncertain', evidence_da_ids=[])
    outcome, model, budget = run(store, candidate, [
        ('assess_item', first), ('search_dialogue', {'query':'bad query', 'scope':'current'}),
        ('assess_item', first), ('search_dialogue', {'query':'QUERY bad BAD', 'scope':'current'}),
        ('stop', {'reason':'Need different evidence'})])
    assert store.searches == ['bad query'] and budget.used == 1 and outcome['lookups'] == 1
    assert not outcome['recordable'] and 'already executed' in outcome['history'][-3]['error']
    menu = next(t for t in model.menus[3] if t['name']=='search_dialogue')
    validator = Draft202012Validator(menu['input_schema'])
    assert 'bad query' in menu['description'] and 'Code rejects repeats' in menu['description']
    assert validator.is_valid({'query':'menu owner','scope':'current'})
    for tools in model.menus:
        for tool in tools:
            assert tool['input_schema']['type']=='object'
            assert not set(tool['input_schema']) & {'oneOf','anyOf','allOf','enum','const','not'}


@pytest.mark.parametrize('query', ['TS3006a', 'TS3006a decisions', 'TS3006a.sys.s.4', 'TS3006b#10 actions', 'earlier records'])
def test_locator_and_type_only_queries_have_no_content(query):
    assert not has_content_query(query)


@pytest.mark.parametrize('query', ['TS3006a technical functions', 'display LCD', 'PM minutes', 'twenty five Euros'])
def test_content_queries_remain_available(query):
    assert has_content_query(query)


def test_empty_history_search_can_be_assessed_or_changed_without_a_full_recheck():
    store, candidate = MemoryStore(), item(); candidate['flags']=['POSSIBLE_PRIOR']
    store.get_prior_items = lambda *a, **k: []
    first = compact(store, candidate, 'accept')
    first['relation_check'].update(result='uncertain', reason='Need history', evidence_da_ids=[])
    outcome, model, budget = run(store, candidate, [('assess_item', first),
        ('get_prior_items', {'query':'menu technical functions','item_type':'actions'}),
        ('assess_relation', {'result':'uncertain','reason':'This query is insufficient to rule out prior work',
                             'quotes':[], 'links':[], 'next_action':'request_clarification'})])
    assert outcome['recordable'] and outcome['state']=='pending' and outcome['fields']['related_items']==[]
    assert 'assess_relation' in model.calls[2]['tools'] and 'search_dialogue' in model.calls[2]['tools']
    assert 'assess_item' not in model.calls[2]['tools'] and budget.used == 1


def test_core_recheck_can_retain_an_existing_passed_conflict_without_resubmission():
    store, candidate = MemoryStore(), item()
    first = compact(store, candidate, 'request_clarification')
    first['statement'].update(result='uncertain',reason='Need core review')
    good = compact(store, candidate)
    outcome, model, _ = run(store, candidate, [('assess_item', first),
        ('assess_core', {'statement':good['statement'],'next_action':'accept'})])
    assert outcome['recordable'] and outcome['state']=='accepted' and len(model.calls)==2
    assert outcome['checks']['conflict']['reason']==first['conflict_check']['reason']
    assert 'retained_checked_conflict' in outcome['history'][-2]['protocol_normalizations']


def test_core_recheck_still_requires_a_new_verdict_for_unresolved_conflict():
    store, candidate = MemoryStore(), item()
    first = compact(store, candidate, 'request_clarification')
    first['conflict_check'].update(result='uncertain', reason='Possible contradiction', affected_fields=['statement'])
    good = compact(store, candidate)
    outcome, model, _ = run(store, candidate, [('assess_item', first),
        ('assess_core', {'statement':good['statement']}), ('stop', {'reason':'Unresolved conflict'})])
    tool = next(t for t in model.menus[1] if t['name']=='assess_core')
    assert 'conflict_check' in tool['input_schema']['required'] and not outcome['recordable']


def test_unsettled_core_review_without_new_action_hint_remains_pending():
    store, candidate = MemoryStore(), item()
    first = compact(store, candidate, 'request_clarification')
    first['statement'].update(result='uncertain', reason='Core cannot be established')
    outcome, model, budget = run(store, candidate, [('assess_item', first),
        ('assess_core', {'statement':first['statement']})])
    assert outcome['state']=='pending' and not outcome['recordable'] and len(model.calls)==2
    assert budget.used == 0 and outcome['checks']['conflict']['result']=='pass'


def test_no_history_scope_with_peer_link_and_missing_finding_is_a_protocol_error():
    store, candidate = MemoryStore(), item('problems'); store.allow_prior=False
    candidate['status']='open'
    wrong=compact(store,candidate);wrong.pop('relation_check')
    wrong['related_items']=[{'id':CUR+'#0','relation':'continues'}]
    good=compact(store,candidate)
    outcome, model, _=run(store,candidate,[('assess_item',wrong),('assess_item',good)])
    assert outcome['state']=='accepted' and outcome['fields']['related_items']==[]
    assert len(model.calls)==2 and 'relation_check' in outcome['history'][0]['error']


def test_participant_lookup_is_not_offered_when_only_statement_or_history_is_unresolved():
    store, candidate = MemoryStore(), item()
    first = compact(store, candidate, 'retrieve'); first['statement'].update(result='uncertain',evidence_da_ids=[])
    _, model, _ = run(store, candidate, [('assess_item', first), ('stop', {'reason':'No assignment evidence'})])
    assert 'get_participants' not in model.calls[1]['tools'] and 'read_dialogue' in model.calls[1]['tools']
