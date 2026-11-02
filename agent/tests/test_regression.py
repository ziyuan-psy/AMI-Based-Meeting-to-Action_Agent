"""Guard regression denominators, independent arms and fail-closed scoring."""
import json
from types import SimpleNamespace

import pytest

from mta import regression as reg


def row(identifier, kind='decisions', text='Keep the LCD'):
    return {'id': identifier, 'type': kind, 'text': text}


def test_scoring_counts_missing_output_as_misses_and_extra_output_as_false_positives():
    gold = [row('g1'), row('g2')]
    assert reg.score_items([], gold, lambda s,g:{})['metrics']['decisions'] == reg.prf(0,0,2)
    value = reg.score_items([row('s1'),row('s2')], gold, lambda s,g:{'s1':'g1'} if s and g else {})
    assert value['metrics']['decisions'] == reg.prf(1,2,2)
    assert [r['id'] for r in value['unmatched']['decisions']['system']] == ['s2']
    assert [r['id'] for r in value['unmatched']['decisions']['gold']] == ['g2']


def test_placeholder_categories_are_unscored_in_both_arms_and_never_rewarded():
    def forbidden(s,g):
        if s or g:
            raise AssertionError('Placeholder must not be aligned')
        return {}
    for system in [[],[row('s1')]]:
        value = reg.score_items(system,[row('g1',text='NA.')],forbidden)
        assert value['metrics']['decisions'] == {'unscored':'NA placeholder category'}
    assert reg.score_items([row('s1')],[],lambda s,g:{})['metrics']['decisions']['sys'] == 1


@pytest.mark.parametrize('bad',[{'s1':'g1','s2':'g1'},{'missing':'g1'},{'s1':'unknown'}])
def test_alignment_must_be_valid_one_to_one_within_type(bad):
    with pytest.raises(ValueError,match='one-to-one'):
        reg.score_items([row('s1'),row('s2')],[row('g1')],lambda s,g:bad if s and g else {})


def test_fewshot_gold_and_its_aligned_output_are_excluded_together():
    result=reg.score_items([row('s1'),row('s2')],[row('g1'),row('example')],
        lambda s,g:{'s1':'g1','s2':'example'} if s and g else {}, {'example'})
    assert result['metrics']['decisions']==reg.prf(1,1,1)
    assert result['matches']=={'s1':'g1'}


def test_p2_pending_is_separate_from_quarantine_and_document_retention():
    def outcome(state,recordable,reason):
        return {'item_key':'x','item_text':'x','state':state,'recordable':recordable,'termination_reason':reason,
                'checks':{},'clarifications':[],'observed_documents':[], 'document_evidence':[],'relation_evidence':[]}
    rows=[outcome('accepted',True,'all_required_checks_passed'),outcome('pending',True,'stop'),outcome('pending',False,'no_progress')]
    rows[0]['document_evidence']=[{'retained':True}]
    result=reg.gate_summary({'items':rows})
    assert (result['accepted'],result['pending'],result['quarantined'],result['recordable_pending'])==(1,2,1,1)
    assert result['documents_retained']==1 and result['by_termination']['no_progress']==1


def test_incomplete_meeting_never_becomes_a_zero_score_in_aggregate(tmp_path):
    complete={'meeting_id':'TS3006a','status':'COMPLETED','arms':{arm:{'metrics':{kind:reg.prf(1,2,3) for kind in reg.TYPES}}
              for arm in ['baseline','current','current_before_p2']}}
    incomplete={'meeting_id':'TS3006b','status':'UNSCORED'}
    result=reg.summarize(tmp_path,[complete,incomplete])
    assert result['eligible_meetings']==['TS3006a'] and result['unscored_meetings']==['TS3006b']
    assert result['aggregate']['current']['decisions']=={**reg.prf(1,2,3),'meetings':1}


def test_failed_earlier_meeting_stops_arm_before_later_history_is_used(tmp_path,monkeypatch):
    monkeypatch.setattr(reg,'verify_inputs',lambda p:{'meetings':['TS3006a','TS3006b']})
    monkeypatch.setattr(reg,'RecordingLLM',lambda trace:SimpleNamespace(usage_snapshot=lambda:{}))
    calls=[]
    def fail(path,*args,**kwargs):
        calls.append(path.stem)
        return {'status':'FAILED','reason':'call_budget_exhausted'}
    monkeypatch.setattr(reg.pipeline,'run_meeting',fail)
    monkeypatch.setattr(reg,'route_inputs',lambda *args:None)
    results=reg.run_arm(tmp_path,'current')
    assert calls==['TS3006a'] and len(results)==1
    saved=json.loads((tmp_path/'current/TS3006a.result.json').read_text())
    assert saved['status']=='FAILED' and 'record_sha256' not in saved
    assert not (tmp_path/'current/TS3006b.result.json').exists()


def test_atomic_checkpoints_do_not_extend_a_long_valid_destination_filename(tmp_path):
    from mta.observation import write_json
    import os
    # The former destination-name-plus-UUID temporary exceeds NAME_MAX on POSIX; on Windows
    # exercise the actual 218-character checkpoint directory that exposed MAX_PATH instead.
    if os.name == 'nt':
        parent=tmp_path / ('d' * max(1,218-len(str(tmp_path))-1))
        target=parent / 'p2_item_123.json'
    else:
        target=tmp_path / ('p' * 230)
    write_json(target, {'checkpoint':'saved'})
    write_json(target, {'checkpoint':'replaced'})
    assert json.loads(target.read_text()) == {'checkpoint':'replaced'}
    assert [p.name for p in target.parent.iterdir()] == [target.name]


@pytest.mark.parametrize('changed',['gold_sha256','baseline_commit','model','config'])
def test_baseline_reuse_refuses_changed_gold_policy_or_model(tmp_path,monkeypatch,changed):
    import copy
    source=tmp_path/'old'; source.mkdir()
    original={k:'same' for k in ['meetings','mode','baseline_commit','input_sha256','gold_sha256']}
    original['current']={'model':'nano','config':{'tokens':1000000}}
    new=copy.deepcopy(original)
    if changed in ('model','config'):
        new['current'][changed]='different'
    else:
        new[changed]='different'
    (source/'study.json').write_text(json.dumps(original))
    monkeypatch.setattr(reg,'verify_inputs',lambda root:new)
    with pytest.raises(ValueError,match='Cannot reuse'):
        reg.reuse_baseline(tmp_path/'new',source)
