"""Offline integrity checks. Online v18 and final v19 response replay stay distinct."""
import ast
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
ONLINE=ROOT/'fixed-v5'
FINAL=ROOT/'final-response-replay-v3'

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def verify(root,values):
    for rel,digest in values.items():assert sha(root/rel)==digest,rel
    return len(values)
def files():return sorted(p for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
def git(*args):return subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),*args],cwd=REPO,text=True,encoding='utf-8').strip()

def main():
    summary=load(ONLINE/'summary.json');study=load(ONLINE/'study.json');replay=load(FINAL/'summary.json')
    final_study=load(FINAL/'study.json')
    assert summary['result']=='PASSED'
    assert replay['result']=='TARGETED_REPLAY_PASSED' and replay['actual_api_calls']==0
    assert final_study['p2_protocol_version']==19
    before=load(ROOT/'before.json');previous=REPO/'agent/results/deadline_trace_audit'
    assert sha(previous/'manifest.json')==before['previous_manifest_sha256']
    previous_count=verify(previous,before['previous_artifact_sha256']);protected=verify(REPO,before['protected_files'])
    preservation={}
    for folder,name in [('smoke_repairs','manifest.json'),('frozen_smoke','manifest.json'),('quality_repairs/release-v9','freeze_manifest.json')]:
        root=REPO/'agent/results'/folder;preservation[folder]=verify(root,load(root/name)['artifact_sha256'])
    for root in ROOT.glob('fixed-v*'):verify(root,load(root/'study.json')['artifact_sha256'])
    source=final_study['source_sha256'];verify(REPO,source);verify(FINAL/'source',source)
    changes=[rel for rel,digest in source.items() if sha(ONLINE/'source'/rel)!=digest]
    assert set(changes)=={'agent/src/mta/config.py','agent/src/mta/assessment_protocol.py'},changes
    for subdir in ('inputs','prior_records'):
        old=ONLINE/('fixed_history' if subdir=='prior_records' else subdir)
        for p in (FINAL/subdir).rglob('*'):
            if p.is_file():assert sha(p)==sha(old/p.relative_to(FINAL/subdir))
    assert git('branch','--show-current')=='agent-development'
    assert git('rev-parse','HEAD')=='3b886f6c0c1520a1c5bc8628da7198a871c25495'
    assert not git('diff','--cached','--name-only')
    subprocess.run(['git','-c','safe.directory='+REPO.as_posix(),'diff','--check','--','agent/src','agent/tests'],cwd=REPO,check=True,capture_output=True)
    log=ROOT/'final-tests.log';match=re.search(r'(\d+) passed in ([\d.]+)s',log.read_text(encoding='utf-8'))
    assert match and int(match[1])==382
    tests={'command':'python -m pytest agent/tests -q','passed':int(match[1]),'seconds':float(match[2]),'exit_code':0,
        'provider_calls':0,'source_sha256':source,'log_sha256':sha(log),
        'test_sha256':{p.relative_to(REPO).as_posix():sha(p) for p in (REPO/'agent/tests').glob('*.py')}}
    save(FINAL/'tests.json',tests)
    evidence={'scope':'Independent online observations only. v19 response replay is separate, not a new sample.',
        'initial_model_findings':[],'same_initial_request_by_version':{}}
    for version,folder in [('v15','fixed-v1'),('v16','fixed-v2'),('v17','fixed-v3'),('v18','fixed-v5')]:
        result=load(ROOT/folder/'summary.json');signatures={c['initial_request_sha256'] for c in result['case_runs']}
        assert len(signatures)==1
        evidence['same_initial_request_by_version'][version]={'sha256':next(iter(signatures)),'observations':len(result['case_runs'])}
        for case in result['case_runs']:
            outcome=case['outcome'];first=next(e for e in outcome['history'] if e.get('outcome')=='checked')
            evidence['initial_model_findings'].append({'version':version,'source':case['source'],
                'requested_action':first['requested_action'],'checks':{k:v['result'] for k,v in first['checks'].items()},
                'terminal_reason':outcome['termination_reason'],'links':outcome['fields']['related_items']})
    evidence['same_first_request_across_all_versions']=len({v['sha256'] for v in evidence['same_initial_request_by_version'].values()})==1
    save(ROOT/'variation_evidence.json',evidence)
    needle=os.environ.get('OPENAI_API_KEY','').encode();assert len(needle)>=12
    counts={'json':0,'jsonl':0,'jsonl_rows':0,'python':0}
    for p in files():
        assert needle not in p.read_bytes(),f'Credential in {p.relative_to(ROOT)}'
        if p.suffix=='.json':load(p);counts['json']+=1
        elif p.suffix=='.jsonl':
            for s in p.read_text(encoding='utf-8').splitlines():
                if s.strip():json.loads(s);counts['jsonl_rows']+=1
            counts['jsonl']+=1
        elif p.suffix=='.py':ast.parse(p.read_text(encoding='utf-8'));counts['python']+=1
    verification={'result':'PASSED','at':datetime.now(timezone.utc).isoformat(),'base_commit':git('rev-parse','HEAD'),
        'branch':'agent-development','committed':False,'staged_files':[],'unit_tests':tests['passed'],
        'latest_online_protocol_version':18,'final_source_protocol_version':19,'final_change_regression':'ZERO_API_CAPTURED_TOOL_ARGUMENT_REPLAY',
        'protected_files_unchanged':protected,'previous_audit_unchanged':previous_count,'preservation':preservation,
        'artifact_parse_checks':counts,'credential_matches':0,'tracked_changes':git('diff','--name-only').splitlines(),
        'preexisting_annotation_change':'revised_gold_output/README.md','gold_scored':False}
    save(ROOT/'verification.json',verification)
    artifact={p.relative_to(ROOT).as_posix():sha(p) for p in files() if p!=ROOT/'manifest.json'}
    save(ROOT/'manifest.json',{'format_version':1,'artifact_sha256':artifact});verify(ROOT,artifact)
    print(json.dumps({'result':'PASSED','artifacts':len(artifact),'tests':tests['passed'],'protected':protected,
        'previous_audit':previous_count,'preservation':preservation,'credential_matches':0,
        'latest_online_version':18,'final_replay_version':19},ensure_ascii=False))

if __name__=='__main__':main()
