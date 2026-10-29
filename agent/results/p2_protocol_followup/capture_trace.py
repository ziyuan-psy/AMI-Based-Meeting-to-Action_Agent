"""Read and preserve the preceding audit before changing P2. No model/gold calls."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
OLD=REPO/'agent/results/deadline_trace_audit'

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    manifest=load(OLD/'manifest.json')['artifact_sha256']
    for rel,digest in manifest.items():assert sha(OLD/rel)==digest,rel
    protected=load(OLD/'before.json')['protected_files']
    for rel,digest in protected.items():assert sha(REPO/rel)==digest,rel
    target=ROOT/'before.json'
    if not target.exists():
        save(target,{'previous_manifest_sha256':sha(OLD/'manifest.json'),'previous_artifact_sha256':manifest,
            'protected_files':protected,'source_and_tests_sha256':{p.relative_to(REPO).as_posix():sha(p)
                for folder in ('agent/src','agent/tests') for p in (REPO/folder).rglob('*.py')}})
    summary=load(OLD/'fixed-v5/summary.json')
    invalid=summary['meetings'][1]['after_trace']['invalid_details']
    v14=load(OLD/'fixed-v5/run/s2/TS3006b.p2.json')['items']
    v9=load(REPO/'agent/results/smoke_repairs/repair-v5/run/s2/TS3006b.p2.json')['items']
    selected=[]
    for version,items in [('v9',v9),('v14',v14)]:
        it=next(i for i in items if i['item_key']=='TS3006b#10')
        selected.append({'version':version,'checks':it['checks'],'initial_context':it['initial_context'],
            'history':it['history'],'fields':it['fields']})
    data={'invalid_submissions':invalid,'cross_meeting_case':selected}
    save(ROOT/'observed.json',data)
    print(json.dumps({'invalid':[{'item':r['item_key'],'action':r['action'],'error':r['error'],
        'raw':r.get('raw_tool_input')} for r in invalid],
        'cross_meeting':[{**{k:s[k] for k in ('version','fields')},
            'first_action':s['history'][0].get('requested_action'),
            'first_checks':{k:v['result'] for k,v in s['history'][0]['checks'].items()}}
            for s in selected]},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
