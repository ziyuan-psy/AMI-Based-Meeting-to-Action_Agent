"""Freeze zero-API candidate regression and a separately labeled scripted S2 check."""
from __future__ import annotations

import argparse,copy,hashlib,json,os,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace as NS
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'agent/src/mta/pipeline.py').is_file())
ROOT=HERE/'41654ab-v2'
PRIOR=REPO/'agent/results/version_selection/213ccfc-v1'
ORIGINAL=REPO/'agent/results/dev16_comparison/213ccfc-v1'
sys.path.insert(0,str(REPO/'agent/src'))
from mta import config,extract,recall_candidate,quality
from mta.observation import write_json,runtime_identity,source_files
from mta.evaluate import make_llm_matcher,TYPES,prf
from mta.regression import score_items
from mta.pipeline import make_record,default_fields,run_meeting,saved_run_complete
from mta.store import Store
from mta.llm import LLM
from mta.trace import StageTrace

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,value):write_json(Path(p),value)
def utc():return datetime.now(timezone.utc).isoformat()
def git(*args):return subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),*args],cwd=REPO).decode().strip()
def route():
    config.DATA=ROOT/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    config.QUALITY_PROFILE=quality.RECALL_CANDIDATE
class Offline:
    model='@30875-fall26-openai/gpt-5-nano'
    def forced_tool(self,*a,**kw):raise AssertionError('Frozen response missing; API is forbidden')

def prepare():
    assert not ROOT.exists();ROOT.mkdir(parents=True)
    assert git('branch','--show-current')=='agent-development' and git('rev-parse','--short','HEAD')=='41654ab'
    (ROOT/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    previous=load(PRIOR/'study.json');meetings=previous['meetings']
    for rel,digest in previous['input_sha256'].items():
        source=PRIOR/rel;assert sha(source)==digest
        target=ROOT/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for mid,digest in previous['gold_sha256'].items():
        source=PRIOR/'scoring_inputs/gold'/(mid+'.json');assert sha(source)==digest
        target=ROOT/'scoring_inputs/gold'/source.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        old=load(PRIOR/'boundaries'/mid/'replay.json')['inputs']
        for name,expected in [('extract_False_'+mid+'.json',old['extraction_sha256']),('consolidate_'+mid+'.json',old['cache_sha256'])]:
            source=ORIGINAL/'baseline/_cache'/name;assert sha(source)==expected
            target=ROOT/'recorded_responses'/name;target.parent.mkdir(exist_ok=True);shutil.copyfile(source,target)
    route();identity=runtime_identity(LLM(),'s2')
    assert identity['model']==previous['runtime']['model']
    for key,value in previous['runtime']['config'].items():assert identity['config'][key]==value,key
    hashes={}
    for path in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
        rel=path.relative_to(REPO).as_posix();target=ROOT/'source'/rel
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target);hashes[rel]=sha(path)
    protected={name:sha(REPO/name) for name in load(PRIOR/'protected_before.json')}
    save(ROOT/'protected_before.json',protected)
    artifacts=load(PRIOR/'manifest.json')['artifact_sha256']
    assert all(sha(PRIOR/name)==digest for name,digest in artifacts.items())
    save(ROOT/'preservation.json',{'version_selection_manifest_sha256':sha(PRIOR/'manifest.json'),'artifact_sha256':artifacts})
    save(ROOT/'scoring_protocol.json',load(PRIOR/'scoring_protocol.json'))
    save(ROOT/'study.json',{'created_at':utc(),'commit':git('rev-parse','HEAD'),'branch':'agent-development',
        'runtime':identity,'source_sha256':hashes,'meetings':meetings,'input_sha256':previous['input_sha256'],
        'gold_sha256':previous['gold_sha256'],'driver_sha256':sha(__file__),
        'execution_kind':'FROZEN_RESPONSE_POSTPROCESSING_REPLAY_AND_SEPARATE_SCRIPTED_SMOKE',
        'live_model_calls':0,'heldout_opened':False,'online_full_trial_completed':False,
        'configured_generation_settings_unchanged':True,'effective_policy_explicit':True})
    print('Prepared frozen candidate inputs; no API calls',flush=True)

def replay():
    route();study=load(ROOT/'study.json');rows=[];locations=load(PRIOR/'scoring_locations.json')
    for mid in study['meetings']:
        target=ROOT/'replay'/mid;target.mkdir(parents=True)
        store=Store(mid,False,ROOT/'unused_history');acts=store.meeting(mid).acts
        cache=target/'cache';cache.mkdir()
        source=ROOT/'recorded_responses'/('extract_False_'+mid+'.json')
        shutil.copyfile(source,cache/source.name)
        trace=StageTrace(target/'stages.jsonl',meeting_id=mid,execution_kind='frozen_response_replay')
        cands=recall_candidate.extract_candidates(Offline(),acts,store.roster(mid),cache=cache/source.name,trace=trace)
        save(target/'extracted.json',cands)
        source=ROOT/'recorded_responses'/('consolidate_'+mid+'.json');saved=load(source)
        key,_=recall_candidate.cache_key(Offline(),cands)
        save(cache/source.name,{'key':key,'runs':saved['runs'][:config.CONSOLIDATE_SAMPLES],
             'origin':{'execution_kind':'414_recorded_capped_samples','source_sha256':sha(source),'not_fresh_model_outputs':True}})
        consolidated=recall_candidate.consolidate(Offline(),cands,cache/source.name,trace)
        normalized=extract.normalize(consolidated,store.participant_ids())
        for item in normalized:item['fields']=default_fields(store,item)
        record=make_record(mid,normalized,[]);save(target/'record.json',record)
        stable=load(PRIOR/'stable_retention'/mid/'inputs.json')
        issue=load(PRIOR/locations[mid]/'inputs.json')
        for typ in TYPES:
            expected=issue['records']['retention_actions_issues'] if typ=='problems' else stable['record']
            assert [i['text'] for i in record if i['type']==typ]==[i['text'] for i in expected if i['type']==typ],(mid,typ)
        save(target/'gold.json',stable['gold'])
        for label,path in [('stable_votes',PRIOR/'stable_retention'/mid/'align_votes.json'),
                           ('issue_votes',PRIOR/locations[mid]/'align_votes.json')]:shutil.copyfile(path,target/(label+'.json'))
        stable_matcher=make_llm_matcher(Offline(),cache=load(target/'stable_votes.json'),votes=3)
        issue_matcher=make_llm_matcher(Offline(),cache=load(target/'issue_votes.json'),votes=3)
        matcher=lambda ss,gg:(issue_matcher if ss and ss[0]['type']=='problems' else stable_matcher)(ss,gg)
        score=score_items(record,stable['gold'],matcher,load(ROOT/'scoring_protocol.json')['fewshot_excluded_ids'])
        save(target/'score.json',score);rows.append({'meeting_id':mid,**score});trace.finish(stage='replay',event='completed',api_calls=0)
    aggregate={}
    for typ in TYPES:
        vals=[r['metrics'][typ] for r in rows if 'unscored' not in r['metrics'][typ]]
        aggregate[typ]=prf(*(sum(v[k] for v in vals) for k in ('tp','sys','gold')))
    aggregate['pooled']=prf(*(sum(aggregate[t][k] for t in TYPES) for k in ('tp','sys','gold')))
    assert [aggregate[t]['f1'] for t in (*TYPES,'pooled')]==[.505,.441,.457,.464]
    save(ROOT/'comparison.json',{'execution_kind':'FIXED_RESPONSE_REPLAY_NOT_ONLINE','meetings':study['meetings'],
         'aggregate':aggregate,'per_meeting':rows,'new_model_calls':0,'new_judge_calls':0})
    print(json.dumps({'replayed':16,'aggregate':aggregate,'api_calls':0}),flush=True)

class SmokeClient:
    """Deterministic transport fixture; its choices are NOT evidence of semantic quality."""
    def __init__(self):self.messages=NS(create=self.create);self.turn=0;self.seen=[]
    def create(self,**kw):
        name=(kw.get('tool_choice') or {}).get('name');self.seen.append(name or 'agent')
        if name=='submit_candidates':
            user=kw['messages'][0]['content'];action='List ONLY actions' in user
            payload={'items':[{'type':'actions','text':'The user interface designer will develop the remote layout and LCD menu.',
                'evidence':[0,1],'owner_ids':['MTD023UID'],'owner_basis':'explicit','deadline':None,
                'deadline_basis':'missing','status':'in_progress'}] if action else []}
        elif name=='submit_abstracts':payload={'abstracts':[{'text':'Layout work was assigned.','items':[0]}]}
        elif name=='submit_final_items':payload={'items':[{'type':'actions','text':'The user interface designer will develop the remote layout and LCD menu.','members':[0]}]}
        else:
            if self.turn==0:name='get_prior_items';payload={'query':'remote layout LCD menu'}
            elif self.turn==1:
                rows=json.loads(kw['messages'][-1]['content'][0]['content'])['results']
                self.prior=rows[0]['id'];name='read_prior_item';payload={'item_id':self.prior,'query':'menu'}
            else:
                initial=json.loads(kw['messages'][0]['content'].split('):\n',1)[1].split('\n\nEvidence',1)[0])
                rows=json.loads(kw['messages'][-1]['content'][0]['content'])['results']
                prior_da=next(r['da_id'] for r in rows if r.get('kind')=='dialogue')
                name='finish';payload={'status':'in_progress','owner':['MTD023UID'],'owner_basis':'explicit',
                    'deadline':None,'deadline_basis':'missing','prior_context_needed':True,
                    'related_items':[{'id':self.prior,'relation':'updates'}],
                    'evidence_da_ids':[Store(initial['current_meeting']).meeting(initial['current_meeting']).acts[0]['id'],prior_da]}
            self.turn+=1
        return NS(content=[NS(type='tool_use',id='offline-'+str(len(self.seen)),name=name,input=payload)],usage=NS(input_tokens=10,output_tokens=5))

def smoke():
    route();rows=[]
    for mid in ('TS3006a','TS3006b'):
        client=SmokeClient();result=run_meeting(config.TRANSCRIPTS/(mid+'.txt'),'s2',LLM(client,model='scripted-offline'),ROOT/'smoke',ROOT/'smoke_cache')
        save(ROOT/'smoke'/(mid+'.result.json'),result)
        assert result['status']=='COMPLETED',result
        assert not result['p2_enabled'] and saved_run_complete(Path(result['output']))
        assert not {'select_record_items','assess_item'}.intersection(client.seen)
        report=Path(result['output']).with_name(mid+'_report.md').read_text(encoding='utf-8')
        assert 'Draft candidate' in report and 'All items passed' not in report
        rows.append(result)
    events=[json.loads(s) for s in (ROOT/'smoke/s2/TS3006b.trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [e['action'] for e in events]==['get_prior_items','read_prior_item','finish']
    assert all('tool_result' in e for e in events if e['action']!='finish')
    save(ROOT/'smoke.json',{'execution_kind':'SCRIPTED_OFFLINE_PLUMBING_TEST_NOT_REAL_MODEL_CASE',
         'gold_in_model_input':False,'real_api_calls':0,'meetings':[r['meeting_id'] for r in rows],
         'checks':['TXT parsing','JSON/report/P2-disabled/clarification artifacts','tool-result trace',
                   'historical item lookup and evidence read','agent finish','shared budget','compatible-profile history']})
    print('Scripted offline S2 a->b check passed; not a real-model case',flush=True)

def finalize():
    route();study=load(ROOT/'study.json')
    assert git('rev-parse','HEAD')==study['commit'] and not git('diff','--cached','--name-only')
    assert sha(__file__)==study['driver_sha256']
    for rel,digest in study['source_sha256'].items():assert sha(REPO/rel)==digest,rel
    for rel,digest in load(ROOT/'protected_before.json').items():assert sha(REPO/rel)==digest,rel
    old=load(ROOT/'preservation.json');assert sha(PRIOR/'manifest.json')==old['version_selection_manifest_sha256']
    assert all(sha(PRIOR/p)==digest for p,digest in old['artifact_sha256'].items())
    suite=ET.parse(ROOT/'tests.xml').getroot();suites=list(suite.iter('testsuite'))
    tests=sum(int(s.get('tests','0')) for s in suites);errors=sum(int(s.get('errors','0'))+int(s.get('failures','0')) for s in suites)
    assert tests>=450 and errors==0
    save(ROOT/'tests.json',{'tests':tests,'failures_or_errors':errors,'real_model_api_calls':0})
    key=os.environ.get('OPENAI_API_KEY','');json_count=jsonl_count=0
    for p in ROOT.rglob('*'):
        if not p.is_file() or '__pycache__' in p.parts:continue
        data=p.read_bytes();assert not (len(key)>8 and key.encode() in data),p
        if p.suffix=='.json':json.loads(data.decode('utf-8'));json_count+=1
        if p.suffix=='.jsonl':
            for line in data.decode('utf-8').splitlines():
                if line.strip():json.loads(line)
            jsonl_count+=1
    save(ROOT/'verification.json',{'status':'PASSED','at':utc(),'replay_meetings':16,'f1':[.505,.441,.457,.464],
        'scripted_smoke_meetings':2,'tests':tests,'json_files':json_count,'jsonl_files':jsonl_count,
        'source_matches_capture':True,'prior_artifacts_unchanged':len(old['artifact_sha256']),
        'protected_files_unchanged':len(load(ROOT/'protected_before.json')),'heldout_opened':False,
        'live_api_calls':0,'online_full_pipeline_completed':False})
    text='''# Recall candidate validation

The selected `recall_candidate_v1` is implemented and explicitly selectable.
It preserves 414 content prompts and stable caps, current type-aware Action
recovery and original Issue hypothesis recovery. The default `development`
profile remains available with full P2. No commit or push was made in this step.

All 16 recorded-response replays ran through the production extractor cache and
candidate consolidator. Per-type texts exactly match the selected earlier views;
all scores replay from actual saved three-vote judgements without API calls:

| View | Action F1 | Decision F1 | Issue F1 | Pooled F1 |
|---|---:|---:|---:|---:|
| 414 reference | 0.474 | 0.441 | 0.429 | 0.449 |
| Candidate recorded-response replay | 0.505 | 0.441 | 0.457 | 0.464 |

The separate a→b smoke is a scripted offline engineering check, not a genuine
model-generated example or evaluation score. It exercises TXT→JSON/report,
budget admission, retrieval→historical evidence read→finish, disabled-P2 sidecars,
and effective-policy recording. Reports identify the candidate as a draft and
do not claim full evidence/conflict verification.

Model/generation/resource settings are unchanged; the selected quality profile
and its effective differences are explicit in study/runtime metadata. Earlier
version-selection artifacts and UI/annotation protection hashes are unchanged.
The held-out set was not opened. Full-suite offline test results are in tests.json.

This is not a new cold-cache online S2 trial. Actual model field/citation quality,
cross-meeting outcomes, and full-run completion still need online validation.
In particular, the restored TS3006a Marketing task needs its assignment-line
citation checked; the new source-review flag requests inspection, not certification.
'''
    (ROOT/'REPORT.md').write_text(text,encoding='utf-8')
    artifacts={p.relative_to(ROOT).as_posix():sha(p) for p in ROOT.rglob('*') if p.is_file() and p.name!='manifest.json' and '__pycache__' not in p.parts}
    save(ROOT/'manifest.json',{'artifact_sha256':artifacts,'created_at':utc()})
    assert all(sha(ROOT/p)==d for p,d in artifacts.items())
    print(json.dumps({'verification':'PASSED','tests':tests,'artifacts':len(artifacts),'live_api_calls':0}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--finalize',action='store_true');args=parser.parse_args()
    if args.finalize:finalize()
    else:prepare();replay();smoke()
