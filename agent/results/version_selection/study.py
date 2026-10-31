"""Immutable inputs and existing-record preservation for component selection."""
from __future__ import annotations
import copy,hashlib,importlib,importlib.util,json,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'agent/src/mta/pipeline.py').is_file())
ROOT=HERE/'213ccfc-v1'
OLD=REPO/'agent/results/dev16_comparison/213ccfc-v1'
ISSUE=REPO/'agent/results/issue_regression/213ccfc-v1'
MEETINGS=[s+c for s in ('ES2011','IS1008','TS3004','TS3006') for c in 'abcd']
sys.path.insert(0,str(REPO/'agent/src'))
def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):
    from mta.observation import write_json
    write_json(Path(p),d)
def utc():return datetime.now(timezone.utc).isoformat()
def git(*args):return subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),*args],cwd=REPO).decode('utf-8').strip()
def package(profile):
    name='_version_selection_'+profile
    if name not in sys.modules:
        path=ROOT/'profiles'/profile/'agent/src/mta'
        spec=importlib.util.spec_from_file_location(name,path/'__init__.py',submodule_search_locations=[str(path)])
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return name
def module(profile,leaf):return importlib.import_module(package(profile)+'.'+leaf)
def route(settings):
    settings.DATA=ROOT/'inputs';settings.TRANSCRIPTS=settings.DATA/'transcripts'
    settings.PARTICIPANTS=settings.DATA/'participants.json';settings.SCENARIO=settings.DATA/'scenario_text.txt'
def verify():
    from mta.llm import LLM
    from mta.observation import runtime_identity
    s=load(ROOT/'study.json');assert git('branch','--show-current')=='agent-development' and git('rev-parse','HEAD')==s['commit']
    assert not git('diff','--cached','--name-only')
    for rel,digest in s['source_at_start'].items():assert sha(REPO/rel)==digest,rel
    for rel,digest in s['input_sha256'].items():assert sha(ROOT/rel)==digest
    identity=runtime_identity(LLM());assert identity['model']==s['runtime']['model'] and identity['config']==s['runtime']['config']
    return identity
def prepare():
    from mta.llm import LLM
    from mta.observation import runtime_identity,source_files
    from mta import config
    from mta.store import Store
    assert not ROOT.exists();ROOT.mkdir(parents=True)
    (ROOT/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    assert git('branch','--show-current')=='agent-development' and git('rev-parse','HEAD').startswith('213ccfc')
    identity=runtime_identity(LLM());previous=load(ISSUE/'study.json')
    assert identity['model']==previous['runtime']['model'] and identity['config']==previous['runtime']['config']
    sources={p.relative_to(REPO).as_posix():sha(p) for p in source_files()+[config.SCHEMA,config.P2_SCHEMA]}
    for rel,digest in sources.items():assert digest==previous['source_sha256'][rel]
    for profile,cohort,part in [('baseline414',OLD,'source_baseline'),('committed213',OLD,'source'),('issue_current',ISSUE,'source')]:
        shutil.copytree(cohort/part,ROOT/'profiles'/profile,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    modified=git('diff','--name-only').splitlines()
    extra=['agent/tests/test_issue_regression.py','agent/tests/fixtures/issue_vote_source_mismatch.json']
    pending={}
    for rel in list(dict.fromkeys(modified+extra)):
        p=REPO/rel;dst=ROOT/'uncommitted_at_start'/rel;dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,dst);pending[rel]=sha(p)
    (ROOT/'uncommitted_at_start.patch').write_bytes(subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),'diff','--binary'],cwd=REPO))
    preservation=copy.deepcopy(load(ISSUE/'preservation.json'))
    preservation['cohorts'][ISSUE.relative_to(REPO).as_posix()]={'manifest_sha256':sha(ISSUE/'manifest.json'),'artifacts':load(ISSUE/'manifest.json')['artifact_sha256']}
    preserved=0
    for rel,proof in preservation['cohorts'].items():
        folder=REPO/rel;assert sha(folder/'manifest.json')==proof['manifest_sha256']
        for name,digest in proof['artifacts'].items():assert sha(folder/name)==digest;preserved+=1
    save(ROOT/'preservation.json',preservation)
    protected=load(ISSUE/'protected_before.json')
    for rel,digest in protected.items():assert sha(REPO/rel)==digest
    save(ROOT/'protected_before.json',protected)
    for rel,digest in previous['input_sha256'].items():
        source=ISSUE/rel;assert sha(source)==digest;dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dst)
    for mid,digest in previous['gold_sha256'].items():
        source=ISSUE/'scoring_inputs/gold'/(mid+'.json');assert sha(source)==digest
        dst=ROOT/'scoring_inputs/gold'/source.name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dst)
    history=ROOT/'frozen_history414';history.mkdir()
    for mid in MEETINGS:shutil.copyfile(OLD/'baseline/s2'/(mid+'.json'),history/(mid+'.json'))
    route(config);legacy=module('baseline414','pipeline');settings=module('baseline414','config');route(settings)
    for mid in MEETINGS:
        normalized=load(OLD/'baseline/attempts'/mid/'normalized.json')
        record=load(history/(mid+'.json'))
        assert [(i['type'],i['text']) for i in normalized]==[(i['type'],i['text']) for i in record if i['type']!='abstract']
        store=Store(mid,True,history);acts=store.meeting(mid).acts
        items=copy.deepcopy(normalized);legacy.flag_items(items,store)
        for n,item in enumerate(items):
            item['run_id']='fixed-414-'+mid
            item['peer_items']=[{'item_key':mid+'#'+str(j),'type':p['type'],'text':p['text']} for j,p in enumerate(items[:n])]
        oldresult=load(OLD/'baseline/attempts'/mid/'result.json')
        attempts=[a for a in oldresult['runtime_control']['attempts'] if a['stage'] in ('extract','consolidate')]
        es=[json.loads(s) for s in (OLD/'baseline/attempts'/mid/'requests.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
        ended=[e['t'] for e in es if e.get('event')=='request_finished' and e.get('request_stage',e.get('stage')) in ('extract','consolidate')]
        started=[e['t'] for e in es if e.get('event')=='request_admitted']
        # Request-finished uses stage=control and request_stage. Fall back to an intentionally conservative total duration.
        debit_seconds=max(ended)-min(started) if ended and started else oldresult['seconds']
        pack={'meeting_id':mid,'normalized':normalized,'p2_items':items,'acts':acts,'baseline_record':record,
            'history_sha256':{m:sha(history/(m+'.json')) for m in store.visible_ids('prior')},
            'upstream_attempts':attempts,'upstream_seconds_debit':debit_seconds,
            'baseline_result_sha256':sha(OLD/'baseline/attempts'/mid/'result.json')}
        save(ROOT/'fixed_inputs'/(mid+'.json'),pack)
    study={'created_at':utc(),'commit':git('rev-parse','HEAD'),'branch':'agent-development','runtime':identity,
        'source_at_start':sources,'existing_uncommitted_sha256':pending,'input_sha256':previous['input_sha256'],
        'gold_sha256':previous['gold_sha256'],'meetings':MEETINGS,'historical_artifacts_preserved':preserved,
        'heldout_opened':False,'gold_passed_to_runtime':False,
        'selection_rule':'Prefer 414a31f unless a same-boundary comparison shows reliable overall benefit without trading away a category. Source/schema validity is mandatory. Do not select modules from whole-run random differences alone.',
        'protocol':'Extraction: lock same TXT and recorded response sets, distinguish code replay from prompt/sampling. Selection: same baseline normalized candidates/evidence. P2: same baseline candidates/flags/evidence/history; upstream usage/time debited. Same gold/matcher/three votes/exclusions. No resampling for better scores.'}
    save(ROOT/'study.json',study);verify()
    print(json.dumps({'prepared':True,'fixed_meetings':16,'preserved_artifacts':preserved,'api_calls':0},ensure_ascii=False))
if __name__=='__main__':prepare()
