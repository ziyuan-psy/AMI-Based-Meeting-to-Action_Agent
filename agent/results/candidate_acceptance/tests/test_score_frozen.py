"""Scoring denominators, cache provenance, scope guards and bounded fake transports."""
import copy,json,sys,threading,time
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import score_frozen as s
from mta.llm import LLM

def item(kind='actions',iid='TS3006a.sys.s.1',text='The UI will specify technical functions.'):
    row={'id':iid,'type':kind,'text':text,'reference':[{'source_type':'dialogue','source':'ami-corpus/dialogueActs/TS3006a.json',
        'locator':'TS3006a.A.dialog-act.vkaraisk.149'}],'status':'adopted' if kind=='decisions' else 'open',
        'prior_context_needed':False,'related_items':[]}
    if kind=='actions':row.update(owner=['MTD023UID'],owner_basis='explicit',deadline=None,deadline_basis='missing')
    return row

@pytest.fixture
def protocol():return s.c.load(s.c.HERE/'scoring_protocol.json')

@pytest.fixture
def job():
    record=[item()];gold=[item(iid='TS3006a.JacquelinePalmer.s.1')]
    return {'meeting_id':'TS3006a','mode':'s2','execution_kind':'SCRIPTED_TEST_NOT_REAL_MODEL',
            'semantics':'none','record':record,'gold_items':gold}

class Client:
    def __init__(self,handler=None):self.messages=NS(create=self.create);self.calls=0;self.handler=handler
    def create(self,**kw):
        self.calls+=1;name=kw['tools'][0]['name']
        data=self.handler(self.calls,name,kw) if self.handler else {'pairs':[{'sys':0,'gold':0}]}
        return NS(content=[NS(type='tool_use',id='test',name=name,input=data)],usage=NS(input_tokens=10,output_tokens=4))

def fake_online(monkeypatch,client):
    monkeypatch.setattr(s,'RecordingLLM',lambda trace:LLM(client,model='scripted-local-only'))

def test_fewshot_excludes_gold_and_its_matched_prediction_together(job):
    record=job['record']+[item(iid='TS3006a.sys.s.2',text='Other task')]
    gold=job['gold_items']+[item(iid='TS3006a.JacquelinePalmer.s.2',text='Other task')]
    result=s.score_items(record,gold,lambda ss,gg:{x['id']:y['id'] for x,y in zip(ss,gg)},[gold[0]['id']])
    assert result['metrics']['actions']==s.prf(1,1,1)
    assert result['matches']=={record[1]['id']:gold[1]['id']}

@pytest.mark.parametrize('placeholder',['NA','N/A','na.',' n/a '])
def test_na_category_has_no_system_or_gold_denominator(job,placeholder):
    gold=[item(text=placeholder)]
    def forbidden(ss,gg):
        if ss or gg:pytest.fail('NA category must not invoke matcher')
        return {}
    result=s.score_items(job['record'],gold,forbidden)
    assert result['metrics']['actions']=={'unscored':'NA placeholder category'}

def test_micro_uses_summed_counts_not_mean_type_f1():
    row={'score':{'metrics':{'actions':s.prf(1,1,1),'decisions':s.prf(0,0,0),'problems':s.prf(0,4,4)}}}
    assert s.aggregate([row])['pooled']==s.prf(1,5,5)

def test_owner_accuracy_uses_only_scored_matches(job):
    result=s.score_items(job['record'],job['gold_items'],lambda ss,gg:{x['id']:y['id'] for x,y in zip(ss,gg)})
    metrics=s.field_metrics(job['record'],job['gold_items'],result)
    assert metrics['owner_exact']=={'correct':1,'total':1,'rate':1.0}
    job['gold_items'][0]['owner']=['MTD022ID']
    assert s.field_metrics(job['record'],job['gold_items'],result)['owner_exact']['correct']==0

@pytest.mark.parametrize('change',['model','prompt','text','type'])
def test_cached_votes_reject_provenance_or_order_changes(tmp_path,protocol,job,change):
    ctl=s.RunControl();path=tmp_path/'votes.json'
    cache=s.VoteCache(path,protocol,'actions',job['record'],job['gold_items'],ctl)
    cache[cache.key]=[[{'sys':0,'gold':0}]]
    p=copy.deepcopy(protocol);record=copy.deepcopy(job['record']);kind='actions'
    if change=='model':p['judge']['name']='different-model'
    elif change=='prompt':p['match_system']+=' changed'
    elif change=='text':record[0]['text']='different task'
    else:kind='problems'
    with pytest.raises(s.ScoringError,match='mismatch'):s.VoteCache(path,p,kind,record,job['gold_items'],ctl)
    ctl.seal()

def test_existing_votes_are_append_only(tmp_path,protocol,job):
    ctl=s.RunControl();cache=s.VoteCache(tmp_path/'votes.json',protocol,'actions',job['record'],job['gold_items'],ctl)
    cache[cache.key]=[[{'sys':0,'gold':0}]]
    with pytest.raises(s.ScoringError):cache[cache.key]=[[]]
    with pytest.raises(s.ScoringError):cache[cache.key]=[]
    assert cache.get(cache.key)==[[{'sys':0,'gold':0}]]
    ctl.seal()

def test_three_votes_are_reused_without_replacement(tmp_path,protocol,job):
    ctl=s.RunControl();path=tmp_path/'votes.json'
    cache=s.VoteCache(path,protocol,'actions',job['record'],job['gold_items'],ctl)
    cache[cache.key]=[[{'sys':0,'gold':0}],[{'sys':0,'gold':0}],[]]
    matched=s.make_llm_matcher(s.OfflineJudge(),cache=cache,votes=3)(job['record'],job['gold_items'])
    assert matched=={'TS3006a.sys.s.1':'TS3006a.JacquelinePalmer.s.1'}
    ctl.seal()

def test_missing_votes_fail_offline_instead_of_inventing_zero_score(tmp_path,protocol,job):
    result=s.score_job(job,protocol,tmp_path,False,None)
    assert result['status']=='FAILED' and result['failure']['type']=='MissingVotes'
    assert result['runtime_control']['attempted_calls']==0 and 'score' not in result

def test_heldout_guard_precedes_every_file_read(tmp_path,monkeypatch):
    def forbidden(*args):pytest.fail('Guard must run before file reads or judge construction')
    monkeypatch.setattr(s.c,'load',forbidden)
    with pytest.raises(s.ScoringError,match='Held-out'):
        s.main(['--run',str(tmp_path/'no-such-run'),'--gold',str(tmp_path/'no-gold'),
                '--series','ES2004','--out',str(tmp_path/'out')])
    assert not (tmp_path/'out').exists()

@pytest.mark.parametrize('locator',['TS3006b.A.dialog-act.test.1','ES2011a.A.dialog-act.test.1'])
def test_prediction_source_scope_is_checked(locator):
    row=item();row['reference'][0]['locator']=locator
    with pytest.raises(s.ScoringError,match='future/other-series'):s.validate_records([row],'TS3006a')

def test_duplicate_gold_ids_are_refused():
    with pytest.raises(s.ScoringError,match='Duplicate'):s.validate_gold([item(),item()],'TS3006a')

def test_missing_native_completion_is_not_treated_as_empty(tmp_path,job):
    path=tmp_path/'TS3006a.json';s.write_json(path,job['record']);s.write_json(path.with_suffix('.run.json'),{'status':'FAILED','result':{'status':'FAILED'}})
    with pytest.raises(s.ScoringError,match='Incomplete prediction'):s.load_native(path,'TS3006a','s2',{})

def test_fake_online_uses_three_calls_under_the_real_controller(tmp_path,monkeypatch,protocol,job):
    client=Client();fake_online(monkeypatch,client)
    result=s.score_job(job,protocol,tmp_path,True,None)
    assert result['status']=='COMPLETED' and result['score']['metrics']['actions']==s.prf(1,1,1)
    assert client.calls==result['runtime_control']['attempted_calls']==3
    assert result['runtime_control']['charged_tokens']==42 and result['runtime_control']['reserved_tokens']==0

def test_transport_retry_shares_call_and_token_budget(tmp_path,monkeypatch,protocol,job):
    def handler(n,*args):
        if n==1:raise ConnectionError('scripted transient')
        return {'pairs':[{'sys':0,'gold':0}]}
    client=Client(handler);fake_online(monkeypatch,client);monkeypatch.setattr(s.config,'RETRY_BACKOFF',0)
    result=s.score_job(job,protocol,tmp_path,True,None)
    assert result['status']=='COMPLETED' and client.calls==result['runtime_control']['attempted_calls']==4
    assert result['runtime_control']['unknown_usage_tokens']>0

def test_model_call_budget_stops_missing_third_vote(tmp_path,monkeypatch,protocol,job):
    client=Client();fake_online(monkeypatch,client);monkeypatch.setattr(s.config,'MAX_MODEL_CALLS',2)
    result=s.score_job(job,protocol,tmp_path,True,None)
    assert result['status']=='BUDGET_EXCEEDED' and client.calls==2 and 'score' not in result
    cache=s.c.load(next((tmp_path/'votes/TS3006a').glob('actions.*.json')))
    assert len(next(iter(cache['votes'].values())))==2

def test_token_budget_refuses_model_before_send(tmp_path,monkeypatch,protocol,job):
    client=Client();fake_online(monkeypatch,client);monkeypatch.setattr(s.config,'MAX_TOTAL_TOKENS',1)
    result=s.score_job(job,protocol,tmp_path,True,None)
    assert result['status']=='BUDGET_EXCEEDED' and client.calls==0

def test_identical_s1_s2_texts_share_the_same_three_judge_votes(tmp_path,monkeypatch,protocol,job):
    client=Client();fake_online(monkeypatch,client)
    first=s.score_job(job,protocol,tmp_path,True,None)
    job['mode']='s1';second=s.score_job(job,protocol,tmp_path,True,None)
    assert first['status']==second['status']=='COMPLETED'
    assert first['score']==second['score'] and client.calls==3
    assert second['runtime_control']['attempted_calls']==0

def test_timeout_saves_result_and_fences_late_reply(tmp_path,monkeypatch,protocol,job):
    release=threading.Event()
    def handler(*args):release.wait(2);return {'pairs':[{'sys':0,'gold':0}]}
    client=Client(handler);fake_online(monkeypatch,client);monkeypatch.setattr(s.config,'REQUEST_TIMEOUT',.02)
    try:
        result=s.score_job(job,protocol,tmp_path,True,None)
        assert result['status']=='TIMED_OUT' and result['runtime_control']['attempted_calls']==1
        path=tmp_path/'per_meeting/TS3006a.s2.json';before=path.read_bytes()
    finally:release.set()
    time.sleep(.05)
    assert path.read_bytes()==before and result['runtime_control']['unknown_usage_tokens']>0

def test_bad_vote_format_fails_without_replacement(tmp_path,monkeypatch,protocol,job):
    client=Client(lambda *args:{'pairs':'invalid'});fake_online(monkeypatch,client)
    result=s.score_job(job,protocol,tmp_path,True,None)
    assert result['status']=='FAILED' and client.calls==1

def test_partial_population_has_no_headline_f1(tmp_path,job):
    result=s.summarize([], [job], {'modes':['s2'],'protocol_digest':'p','scope':'TEST','semantics':'none','generation':{'cache_policy':'cold'}},tmp_path)
    assert result['status']=='INCOMPLETE' and result['modes']['s2']['metrics'] is None
    assert result['unscored_jobs']==['TS3006a.s2']

def test_unmapped_relation_targets_are_counted_separately(job):
    job['record'][0]['related_items']=[{'id':'old.1','relation':'continues'},{'id':'old.2','relation':'continues'}]
    row={'score':{'matches':{job['record'][0]['id']:job['gold_items'][0]['id']}}}
    result=s.relation_metrics([row],[job]);assert result['sys']==2 and result['tp']==0

def semantic_job(job):
    original=s.c.ROOT/'run/s2/TS3006a.json'
    job.update(semantics='all',prediction=original,metadata=s.c.load(original.with_suffix('.run.json')))
    job['record'] += [{'id':'TS3006a.sys.s.2','type':'abstract','text':'First summary point.','reference':[]},
                      {'id':'TS3006a.sys.s.3','type':'abstract','text':'Second summary point.','reference':[]}]
    job['gold_items'] += [{'id':'TS3006a.JacquelinePalmer.s.2','type':'abstract','text':'First reference point.','reference':[]},
                         {'id':'TS3006a.JacquelinePalmer.s.3','type':'abstract','text':'Second reference point.','reference':[]}]
    return job

def semantic_handler(n,name,kw):
    if name=='submit_matches':return {'pairs':[{'sys':0,'gold':0}]}
    if name=='submit_verdicts':return {'verdicts':[{'item':0,'statement':'supported','owner':'supported','deadline':'na'}]}
    key='sentences' if name=='submit_faithfulness' else 'points';level='supported' if key=='sentences' else 'covered'
    return {'score':5,key:[{'index':0,'verdict':level},{'index':1,'verdict':level}]}

def test_optional_semantic_scores_use_same_bounded_client(tmp_path,monkeypatch,protocol,job):
    _,p,helpers=s.frozen_protocol();job=semantic_job(job);client=Client(semantic_handler);fake_online(monkeypatch,client)
    result=s.score_job(job,p,tmp_path,True,helpers)
    assert result['status']=='COMPLETED' and client.calls==6
    assert result['semantic']['summary']['faithfulness']['score']==5
    assert result['semantic']['support'][0]['verdict']['statement']=='supported'

def test_duplicate_summary_indexes_fail_even_with_correct_total_count(tmp_path,monkeypatch,protocol,job):
    _,p,helpers=s.frozen_protocol();job=semantic_job(job)
    def handler(n,name,kw):
        result=semantic_handler(n,name,kw)
        if name=='submit_faithfulness':result['sentences'][1]['index']=0
        return result
    client=Client(handler);fake_online(monkeypatch,client)
    result=s.score_job(job,p,tmp_path,True,helpers)
    assert result['status']=='FAILED' and 'Duplicate/missing summary' in result['failure']['reason']

def test_manifest_rejects_changed_vote_file(tmp_path):
    (tmp_path/'votes.json').write_text('{}',encoding='utf-8')
    s.write_json(tmp_path/'manifest.json',{'artifact_sha256':{'votes.json':s.sha256(tmp_path/'votes.json')}})
    assert s.verify_manifest(tmp_path)==1
    (tmp_path/'votes.json').write_text('{"changed":true}',encoding='utf-8')
    with pytest.raises(s.ScoringError,match='manifest mismatch'):s.verify_manifest(tmp_path)
