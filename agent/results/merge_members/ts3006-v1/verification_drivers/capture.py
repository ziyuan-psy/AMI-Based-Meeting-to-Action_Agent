"""Capture actual D-stage responses and preservation hashes. No API, no gold."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
OLD=REPO/'agent/results/select_reason/ts3006-v1'
SB16=REPO/'agent/results/s2_dev16/787d88e-v1'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    assert not (ROOT/'before.json').exists()
    cohorts=[]
    for root in (OLD,SB16):
        values=load(root/'manifest.json')['artifact_sha256']
        for rel,digest in values.items():assert sha(root/rel)==digest,rel
        cohorts.append({'path':root.relative_to(REPO).as_posix(),'manifest_sha256':sha(root/'manifest.json'),
            'artifact_sha256':values,'parent_files':{p.relative_to(REPO).as_posix():sha(p) for p in root.parent.iterdir() if p.is_file()}})
    protected=load(OLD/'protected_before.json')
    for rel,digest in protected.items():assert sha(REPO/rel)==digest,rel
    save(ROOT/'before.json',{'base_commit':'787d88e34d803a3bcafa4817720634f2dd74b50e','cohorts':cohorts,
        'protected_files':protected,'source_before':load(OLD/'study.json')['source_sha256']})
    meta=load(OLD/'run/s2/TS3006d.run.json');events=[json.loads(s) for s in (OLD/'run/s2/TS3006d.stages.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    replies=[{'attempt':e['attempt'],'seq':e['seq'],'raw_response':t['input']} for e in events
        if e.get('event')=='request_finished' and e.get('request_stage')=='consolidate'
        for t in e['response_tools'] if t['name']=='submit_final_items']
    assert {r['attempt'] for r in replies}=={17,18,19}
    target=REPO/'agent/tests/fixtures/consolidation_bad_members.json';assert not target.exists()
    save(target,{'origin':'Actual TS3006d consolidation responses, select_reason/ts3006-v1',
        'trace_sha256':sha(OLD/'run/s2/TS3006d.stages.jsonl'),'model':meta['model']['name'],
        'candidates':load(Path(meta['checkpoints']['extracted'])),'responses':replies})
    save(ROOT/'capture.json',{'fixture':target.relative_to(REPO).as_posix(),'fixture_sha256':sha(target),'new_api_calls':0})
    print(json.dumps({'preserved_cohorts':len(cohorts),'preserved_artifacts':sum(len(c['artifact_sha256']) for c in cohorts),'captured_responses':len(replies),'new_api_calls':0}))

if __name__=='__main__':main()
