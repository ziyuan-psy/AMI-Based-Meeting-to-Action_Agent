"""Preserve the old cohort and capture the actual failing selection arguments. No API."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
OLD=REPO/'agent/results/s2_dev16/787d88e-v1'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    assert not (ROOT/'before.json').exists(),'Do not overwrite capture'
    manifest=load(OLD/'manifest.json')['artifact_sha256']
    for rel,digest in manifest.items():assert sha(OLD/rel)==digest,rel
    protected=load(OLD/'protected_before.json')
    for rel,digest in protected.items():assert sha(REPO/rel)==digest,rel
    parents={p.relative_to(REPO).as_posix():sha(p) for p in OLD.parent.iterdir() if p.is_file()}
    save(ROOT/'before.json',{'base_commit':'787d88e34d803a3bcafa4817720634f2dd74b50e',
        'previous_manifest_sha256':sha(OLD/'manifest.json'),'previous_artifact_sha256':manifest,
        'previous_parent_files':parents,'protected_files':protected,
        'initial_source_sha256':load(OLD/'study.json')['source_sha256']})
    meta=load(OLD/'run/s2/TS3006b.run.json')
    pool=load(Path(meta['checkpoints']['consolidated']))
    sys.path.insert(0,str(REPO/'agent/src'))
    from mta import config
    from mta.store import Store
    config.DATA=OLD/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    acts=Store('TS3006b',True,OLD/'run/s2').meeting('TS3006b').acts
    events=[json.loads(line) for line in (OLD/'run/s2/TS3006b.stages.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    attempts=[e for e in events if e.get('stage')=='select' and e.get('event')=='attempt']
    assert len(attempts)==2
    selected={d for i in pool for d in i['evidence_da_ids']}
    fixture={'origin':'s2_dev16/787d88e-v1 TS3006b, both actual selection submissions',
        'source_trace_sha256':sha(OLD/'run/s2/TS3006b.stages.jsonl'),'model':meta['model']['name'],
        'pool':pool,'acts':[a for a in acts if a['id'] in selected],'input':attempts[0]['input'],
        'responses':[{'attempt':e['attempt'],'seq':e['seq'],'error':e['error'],'raw_response':e['raw_response']} for e in attempts]}
    target=REPO/'agent/tests/fixtures/selection_missing_reason.json'
    assert not target.exists();save(target,fixture)
    save(ROOT/'capture.json',{'fixture':target.relative_to(REPO).as_posix(),'fixture_sha256':sha(target),
        'actual_responses':2,'pool_items':len(pool),'new_api_calls':0})
    print(json.dumps({'preserved_artifacts':len(manifest),'protected_files':len(protected),'actual_responses':2,'new_api_calls':0}))

if __name__=='__main__':main()
