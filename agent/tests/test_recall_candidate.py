"""Candidate policy, isolation, source guards and pipeline checks without API calls."""
import copy
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mta import baseline_prompts, config, extract, pipeline, quality, recall_candidate
from mta.agent import MeetingBudget, _run_legacy_agent
from mta.control import RunControl, RunStopped
from mta.llm import LLM
from mta.observation import runtime_identity
from mta.trace import StageTrace, Trace
from test_consolidate import cand
from test_observation import FixtureClient
from test_p2 import MemoryStore, item, CUR, D1, D2
from test_pipeline import Fake


@pytest.fixture
def candidate_profile(monkeypatch):
    monkeypatch.setattr(config, 'QUALITY_PROFILE', quality.RECALL_CANDIDATE)


def test_prompts_equal_the_frozen_414_source_and_features_are_explicit(candidate_profile):
    source = runpy.run_path(str(config.ROOT / 'results/version_selection/213ccfc-v1/profiles/baseline414/agent/src/mta/prompts.py'))
    assert baseline_prompts.EXTRACT_SYSTEM == source['EXTRACT_SYSTEM']
    assert baseline_prompts.CONSOLIDATE_SYSTEM == source['CONSOLIDATE_SYSTEM']
    policy = quality.effective('s2')
    assert not any(policy[k] for k in ('p2_enabled', 'selection_enabled', 'decision_second_pass'))
    assert config.P2_ENABLED and config.ITEM_SELECTION_ENABLED and config.DECISION_RECONSOLIDATE
    assert policy['cap_order'] == '414_stable'
    identity = runtime_identity(LLM(FixtureClient(), model='offline'), 's2')
    assert identity['quality_policy'] == policy
    assert identity['prompts']['EXTRACT_SYSTEM']['text'] == source['EXTRACT_SYSTEM']


def test_type_recovery_preserves_existing_order_and_recovers_source_problem_despite_lineage():
    a = cand('actions', 'UI researches functions', 'm.A.1', 'UI')
    problem = cand('problems', 'Missing cost prevents evaluation', 'm.A.2')
    decision = {**a, 'type': 'decisions', 'text': 'Technical functions will be researched'}
    broad = {**problem, 'text': 'There are design concerns', 'candidate_indexes': [1]}
    runs = [[decision, broad] for _ in range(3)]
    original = copy.deepcopy(runs)
    out = recall_candidate.finish_samples(runs, [a, problem])
    assert [i['text'] for i in out] == [decision['text'], broad['text'], a['text'], problem['text']]
    assert out[-1]['restored_problem'] and out[-2]['restored']
    assert runs == original
    assert extract.restore_problems([broad], [a, problem]) == [broad]  # development lineage rule retained


def test_stable_cap_does_not_promote_new_owner_over_existing_tasks():
    tasks = [cand('actions', f'Existing task {n}', f'm.A.{n}', 'UI') for n in range(config.MAX_ITEMS['actions'])]
    recovered = cand('actions', 'A later different owner task', 'm.B.1', 'ID')
    out = recall_candidate.finish_samples([copy.deepcopy(tasks) for _ in range(3)], tasks + [recovered])
    assert [i['text'] for i in out] == [i['text'] for i in tasks]


def test_candidate_merger_keeps_member_type_guard_and_cache_trace(tmp_path):
    class Groups:
        model = 'offline'
        def forced_tool(self, system, user, *args, **kwargs):
            assert system == baseline_prompts.CONSOLIDATE_SYSTEM
            return {'items': [{'type': 'decisions', 'text': 'Use batteries', 'members': [0]},
                              {'type': 'problems', 'text': 'Bad members', 'members': [{'index': 1}]}]}
    candidates = [cand('decisions', 'Use batteries', 'm.A.1'), cand('problems', 'Unknown cost', 'm.A.2')]
    path = tmp_path / 'cache.json'
    trace = StageTrace()
    result = recall_candidate.consolidate(Groups(), candidates, path, trace)
    assert [i['text'] for i in result] == ['Use batteries', 'Unknown cost']
    assert len(json.loads(path.read_text(encoding='utf-8'))['runs']) == config.CONSOLIDATE_SAMPLES
    rejected = [x for e in trace.events if e.get('event') == 'attempt' for d in e['events'] for x in d.get('rejected', [])]
    assert any(r['reason'] == 'members_must_be_candidate_indexes' for r in rejected)
    class NoCalls:
        model = 'offline'
        def forced_tool(self, *a, **kw):
            raise AssertionError('cache should prevent every model call')
    assert recall_candidate.consolidate(NoCalls(), candidates, path) == result


def test_candidate_pipeline_reports_effective_policy_without_claiming_p2_acceptance(tmp_path, candidate_profile):
    client = Fake([])
    result = pipeline.run_meeting(config.TRANSCRIPTS / 'TS3006a.txt', 's1', LLM(client), tmp_path / 'out', tmp_path / 'cache')
    assert result['status'] == 'COMPLETED', result
    assert result['quality_profile'] == quality.RECALL_CANDIDATE and not result['p2_enabled']
    assert not {'select_record_items', 'assess_item'}.intersection(client.efforts)
    record_path = Path(result['output'])
    meta = json.loads(Path(result['run_metadata']).read_text(encoding='utf-8'))
    assert meta['config']['P2_ENABLED'] and not meta['quality_policy']['p2_enabled']
    assert Path(meta['cache_directory']).name == quality.RECALL_CANDIDATE
    assert pipeline.saved_run_complete(record_path)
    sidecar = json.loads(record_path.with_suffix('.p2.json').read_text(encoding='utf-8'))
    assert not sidecar['enabled'] and not sidecar['items']
    report = record_path.with_name('TS3006a_report.md').read_text(encoding='utf-8')
    assert 'Draft candidate' in report and 'All items passed' not in report
    assert result['runtime_control']['limits']['tokens'] == config.MAX_TOTAL_TOKENS


def test_profile_changes_invalidate_skip_and_mixed_history_is_blocked(tmp_path, candidate_profile, monkeypatch):
    first = pipeline.run_meeting(config.TRANSCRIPTS / 'TS3006a.txt', 's2', LLM(Fake([])), tmp_path, tmp_path / 'cache')
    assert first['status'] == 'COMPLETED'
    path = Path(first['output'])
    monkeypatch.setattr(config, 'QUALITY_PROFILE', quality.DEVELOPMENT)
    assert not pipeline.saved_run_complete(path)
    client = Fake([])
    result = pipeline.run_meeting(config.TRANSCRIPTS / 'TS3006b.txt', 's2', LLM(client), tmp_path, tmp_path / 'cache')
    assert result['status'] == 'BLOCKED' and result['reason'] == 'prior_quality_profile_mismatch'
    assert result['runtime_control']['attempted_calls'] == 0


def test_candidate_series_agent_can_retrieve_and_stop_with_same_series_prior(tmp_path, candidate_profile):
    first = pipeline.run_meeting(config.TRANSCRIPTS / 'TS3006a.txt', 's2', LLM(Fake([])), tmp_path, tmp_path / 'cache')
    assert first['status'] == 'COMPLETED'
    # The first two extraction lines are already delivered. Relation targets must come from the actual lookup.
    from mta.store import Store
    current = Store('TS3006b').meeting('TS3006b').acts[0]['id']
    def finish(kw):
        prior = json.loads(kw['messages'][-1]['content'][0]['content'])['results'][0]['id']
        return {'status': 'in_progress', 'owner': ['MTD023UID'], 'owner_basis': 'explicit',
                'deadline': None, 'deadline_basis': 'missing', 'related_items': [{'id': prior, 'relation': 'updates'}],
                'prior_context_needed': True, 'evidence_da_ids': [current]}
    fake = Fake([('get_prior_items', {'query': 'remote layout LCD menu'}), ('finish', finish)])
    result = pipeline.run_meeting(config.TRANSCRIPTS / 'TS3006b.txt', 's2', LLM(fake), tmp_path, tmp_path / 'cache')
    assert result['status'] == 'COMPLETED', result
    events = [json.loads(s) for s in (tmp_path / 's2/TS3006b.trace.jsonl').read_text(encoding='utf-8').splitlines()]
    lookup = next(e for e in events if e.get('action') == 'get_prior_items')
    assert lookup['tool_result']['results'][0]['id'].startswith('TS3006a.')
    assert json.loads(lookup['delivered_result']) == lookup['tool_result']
    assert fake.agent_calls == ['get_prior_items', 'finish']


def test_candidate_followup_must_read_new_support_before_citing_it():
    store = MemoryStore()
    def finish(da):
        return {'status': 'open', 'owner': ['P2'], 'owner_basis': 'explicit', 'deadline': 'next meeting',
                'deadline_basis': 'explicit', 'prior_context_needed': False, 'related_items': [], 'evidence_da_ids': [da]}
    fake = Fake([('finish', finish(D2)), ('read_dialogue', {'meeting_id': CUR, 'da_id': D2}), ('finish', finish(D2))])
    trace = Trace()
    result = _run_legacy_agent(LLM(fake), store, item(), MeetingBudget(), trace, CUR + '#0', observed_only=True)
    assert result and result['reference'][0]['locator'] == D2
    assert trace.events[0]['outcome'] == 'invalid_finish'
    assert trace.events[1]['tool_result']['results'][0]['da_id'] == D2


def test_oversized_lookup_cannot_authorize_a_hidden_dialogue_citation():
    store = MemoryStore()
    def oversized(*args):
        return [{'da_id': D2, 'text': 'x' * 7000, 'role': 'UI', 'participant': 'P2'}]
    store.read_dialogue = oversized
    def finish(da):
        return {'status': 'open', 'owner': ['P2'], 'owner_basis': 'explicit', 'deadline': 'next meeting',
                'deadline_basis': 'explicit', 'prior_context_needed': False, 'related_items': [], 'evidence_da_ids': [da]}
    fake = Fake([('read_dialogue', {'meeting_id': CUR, 'da_id': D2}), ('finish', finish(D2)), ('finish', finish(D1))])
    trace = Trace()
    result = _run_legacy_agent(LLM(fake), store, item(), MeetingBudget(), trace, CUR + '#0', observed_only=True)
    assert result and result['reference'][0]['locator'] == D1
    delivery = json.loads(trace.events[0]['delivered_result'])
    assert delivery['results'] == [] and delivery['omitted_results'] == 1
    assert trace.events[0]['tool_result']['results'][0]['da_id'] == D2
    assert trace.events[1]['outcome'] == 'invalid_finish'


def test_initial_evidence_beyond_the_twelve_delivered_lines_is_not_authorized():
    store = MemoryStore()
    candidate = item()
    # The repeated initial line is delivered 12 times; D2 is an unread thirteenth seed.
    candidate['evidence_da_ids'] = [D1] * 12 + [D2]
    def finish(da):
        return {'status': 'open', 'owner': ['P2'], 'owner_basis': 'explicit', 'deadline': 'next meeting',
                'deadline_basis': 'explicit', 'prior_context_needed': False, 'related_items': [], 'evidence_da_ids': [da]}
    fake = Fake([('finish', finish(D2)), ('finish', finish(D1))])
    trace = Trace()
    result = _run_legacy_agent(LLM(fake), store, candidate, MeetingBudget(), trace, CUR + '#0', observed_only=True)
    assert result and result['reference'][0]['locator'] == D1
    assert trace.events[0]['outcome'] == 'invalid_finish'


def test_candidate_model_calls_still_use_whole_meeting_admission(tmp_path, candidate_profile, monkeypatch):
    monkeypatch.setattr(config, 'MAX_MODEL_CALLS', 0)
    result = pipeline.run_meeting(config.TRANSCRIPTS / 'TS3006a.txt', 's1', LLM(Fake([])), tmp_path, tmp_path / 'cache')
    assert result['status'] == 'BUDGET_EXCEEDED' and result['runtime_control']['attempted_calls'] == 0
    assert Path(result['partial_artifact']).exists() and not (tmp_path / 's1/TS3006a.json').exists()


def test_candidate_sample_calls_do_not_receive_a_fresh_budget(monkeypatch):
    monkeypatch.setattr(config, 'MAX_MODEL_CALLS', 1)
    candidates = [cand('decisions', 'Use batteries', 'm.A.1'), cand('problems', 'Unknown cost', 'm.A.2')]
    control = RunControl()
    def respond(**kwargs):
        return NS(content=[NS(type='tool_use', id='merge', name='submit_final_items', input={
            'items': [{'type': 'decisions', 'text': 'Use batteries', 'members': [0]}]})],
                  usage=NS(input_tokens=10, output_tokens=5))
    client = NS(messages=NS(create=respond))
    try:
        with pytest.raises(RunStopped) as error:
            recall_candidate.consolidate(LLM(client).scoped(control), candidates)
        assert error.value.failure['code'] == 'call_budget_exhausted'
        assert control.snapshot()['attempted_calls'] == 1
    finally:
        control.seal()


def test_unknown_profile_fails_before_any_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'QUALITY_PROFILE', 'mistyped')
    client = Fake([])
    result = pipeline.run_meeting(config.TRANSCRIPTS / 'TS3006a.txt', 's2', LLM(client), tmp_path)
    assert result['reason'] == 'invalid_quality_profile' and client.n_calls == 0
