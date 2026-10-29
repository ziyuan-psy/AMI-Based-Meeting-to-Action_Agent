"""Fixed live-sample regressions, Decision-only replacement, budgets and causal diagnostics; no API calls."""
import json
from pathlib import Path
import shutil
import sys
import runpy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mta import config, pipeline
from mta.control import Limits, RunControl, RunStopped
from mta.decision_audit import explain, score
from mta.extract import _cap, _merge_groups, consolidate, normalize, vote_items
from mta.llm import LLM
from mta.trace import StageTrace
from test_consolidate import cand
from test_observation import FixtureClient

BUNDLE=config.ROOT/'results/decision_studies/decision-v3'


@pytest.fixture(autouse=True)
def captured_prompt_contract(monkeypatch):
    """These are historical-response ablations, not responses to today's extraction prompt."""
    from mta import extract
    prompts=runpy.run_path(str(config.ROOT/'results/quality_repairs/release-v9/source/agent/src/mta/prompts.py'))
    for name in ('EXTRACT_SYSTEM','CONSOLIDATE_SYSTEM','DECISION_CONSOLIDATE_SYSTEM'):
        monkeypatch.setattr(extract,name,prompts[name])

def decision(text,members,evidence='same-line'):
    return {**cand('decisions',text,evidence),'candidate_indexes':members}

class CachedOnly:
    def __init__(self):
        self.model=json.loads((BUNDLE/'study.json').read_text(encoding='utf8'))['captured_model']['name']
    def forced_tool(self,*args,**kwargs):
        raise AssertionError('A captured response is missing')

def seed(tmp_path,mid='TS3006b',decisions=True):
    case=BUNDLE/mid
    cache=tmp_path/f'consolidate_{mid}.json'
    shutil.copyfile(case/'consolidate.json',cache)
    if decisions:
        shutil.copyfile(case/'consolidate.decisions.json',cache.with_name(cache.stem+'.decisions.json'))
    return cache,json.loads((case/'candidates.json').read_text(encoding='utf8'))

def test_fine_choices_receive_support_from_broad_sample_without_consuming_each_others_vote():
    audience=decision('Target younger adults',[0]);feature=decision('Include voice recognition',[1])
    broad=decision('Target younger adults and include voice recognition',[0,1])
    runs=[[audience,feature],[broad],[broad]]
    assert len(vote_items(runs))==1
    trace=StageTrace()
    assert vote_items(runs,trace,refine_decisions=True)==[audience,feature]
    event=next(e for e in trace.events if e['event']=='decision_vote')
    assert [c['votes'] for c in event['clusters']]==[3,3]

def test_different_choices_on_same_evidence_stay_distinct():
    a,b=decision('Use batteries',[0]),decision('Exclude teletext',[1])
    assert vote_items([[a,b]]*3,refine_decisions=True)==[a,b]

def test_one_sample_cannot_manufacture_a_majority_with_duplicate_groups():
    a,b=decision('Proposal',[0]),decision('Other choice',[1])
    assert vote_items([[a,a],[b],[b]],refine_decisions=True)==[b]

def test_partial_refinement_retains_unrepresented_content():
    fine,broad=decision('Batteries',[0]),decision('Batteries and hatch',[0,1])
    assert broad in vote_items([[fine],[broad],[broad]],refine_decisions=True)

def test_decision_cap_is_unchanged():
    choices=[decision(f'Choice {i}',[i],str(i)) for i in range(10)]
    assert len(_cap(vote_items([choices]*3,refine_decisions=True)))==config.MAX_ITEMS['decisions']==7

def test_new_pass_recovers_real_lcd_case_and_preserves_actions_and_issues(tmp_path):
    cache,candidates=seed(tmp_path)
    llm=CachedOnly();trace=StageTrace()
    before=consolidate(llm,candidates,cache)
    after=consolidate(llm,candidates,cache,trace,enable_decision_pass=True)
    assert not any('speech recognition' in i['text'] for i in before if i['type']=='decisions')
    assert any('speech recognition' in i['text'] for i in after if i['type']=='decisions')
    for typ in ['actions','problems']:
        assert [i for i in before if i['type']==typ]==[i for i in after if i['type']==typ]
    assert any(e['stage']=='decision_consolidate' and e['event']=='decision_vote' for e in trace.events)
    assert any(e['stage']=='decision_consolidate' and e['event']=='replaced' for e in trace.events)

def test_new_pass_shares_meeting_call_budget_instead_of_receiving_extra_allowance(tmp_path):
    cache,candidates=seed(tmp_path,decisions=False)
    limits=Limits(calls=0,tokens=1000000,request_seconds=1,meeting_seconds=5,inflight=2,retries=0,backoff=0)
    control=RunControl(limits)
    llm=LLM(FixtureClient(),model=CachedOnly().model).scoped(control)
    try:
        with pytest.raises(RunStopped,match='call_budget_exhausted'):
            consolidate(llm,candidates,cache,enable_decision_pass=True)
        assert control.snapshot()['attempted_calls']==0
        assert control.failure['stage']=='decision_consolidate'
        assert not cache.with_name(cache.stem+'.decisions.json').exists()
    finally:
        control.seal()

def test_pipeline_publishes_valid_schema_with_new_decision_stage(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'P2_ENABLED',False)  # isolate the step-four consolidation regression
    monkeypatch.setattr(config,'ITEM_SELECTION_ENABLED',False)  # Frozen step-four ablation, before project selection.
    cache_dir=tmp_path/'cache';cache_dir.mkdir()
    seed(cache_dir,'TS3006a')
    frozen=config.ROOT/'results/dev_baselines/trace-v1-ts3006ab/_cache/extract_False_TS3006a.json'
    shutil.copyfile(frozen,cache_dir/frozen.name)
    monkeypatch.setattr(pipeline,'run_agent',lambda *args,**kwargs:None)
    result=pipeline.run_meeting(config.TRANSCRIPTS/'TS3006a.txt','s1',LLM(FixtureClient(),model=CachedOnly().model),tmp_path,cache_dir)
    assert result['status']=='COMPLETED',result
    record=json.loads(Path(result['output']).read_text(encoding='utf8'))
    assert sum(i['type']=='decisions' for i in record)==7
    assert all('candidate_indexes' not in i for i in record)
    events=[json.loads(l) for l in Path(result['stage_trace']).read_text(encoding='utf8').splitlines()]
    assert any(e['stage']=='decision_consolidate' and e['event']=='replaced' for e in events)

def test_precision_recall_remains_one_to_one_for_compound_choices():
    items=[decision('Batteries and hatch',[0,1])]
    gold=[{'id':'g1','type':'decisions','text':'Batteries'},{'id':'g2','type':'decisions','text':'Hatch'}]
    result=score(items,gold,lambda ss,gg:{ss[0]['id']:gg[0]['id']} if ss else {})
    assert result['metrics']['decisions']['tp']==1 and result['metrics']['decisions']['recall']==0.5

def test_diagnosis_distinguishes_vote_loss_from_lost_granularity():
    item=decision('Batteries',[0]);gold=[{'id':'g','type':'decisions','text':item['text']}]
    coverage={'matches':[{'candidate':0,'gold_id':'g'}]};detail=StageTrace()
    _merge_groups([{'type':'decisions','text':item['text'],'members':[0]}],[item],detail)
    arm={'cache':{'run_traces':[[{'events':detail.events}]]*3},'voted':[],'normalized':[]}
    assert explain(gold,coverage,[item],arm,{'pairs':{}})[0]['attribution']=='majority_vote'
    arm.update(voted=[item],normalized=[item])
    assert explain(gold,coverage,[item],arm,{'pairs':{}})[0]['attribution']=='merge_granularity_or_wording'

def test_alignment_votes_cannot_be_reused_for_changed_text_lists():
    import importlib.util
    script=config.ROOT/'scripts/decision_study.py'
    spec=importlib.util.spec_from_file_location('study_vote_reuse_test',script)
    study=importlib.util.module_from_spec(spec);spec.loader.exec_module(study)
    source=config.ROOT/'results/decision_studies/decision-v1/TS3006b'
    sources={arm:json.loads((source/f'{arm}.json').read_text(encoding='utf8')) for arm in ['before','after']}
    scores=json.loads((source/'evaluation.json').read_text(encoding='utf8'))
    items=json.loads((BUNDLE/'TS3006b/after.json').read_text(encoding='utf8'))['normalized']
    reused=study.reuse_alignment(items,sources,scores)
    assert reused['metrics']['decisions']['tp']==5
    next(i for i in items if i['type']=='decisions')['text']='A different unsupported claim'
    with pytest.raises(ValueError,match='fresh voted alignment is required'):
        study.reuse_alignment(items,sources,scores)
