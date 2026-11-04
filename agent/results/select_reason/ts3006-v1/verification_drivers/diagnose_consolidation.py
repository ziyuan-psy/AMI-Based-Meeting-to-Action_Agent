"""Preserve and reproduce the new D-stage blocker without changing consolidation code or calling a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path.insert(0,str(REPO/'agent/src'))
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(root):
    from mta import extract
    meta=load(root/'run/s2/TS3006d.run.json');result=load(root/'run/TS3006d.result.json')
    candidates=load(Path(meta['checkpoints']['extracted']))
    events=[json.loads(s) for s in (root/'run/s2/TS3006d.stages.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    replay=[]
    for event in events:
        if event.get('event')!='request_finished' or event.get('request_stage')!='consolidate':continue
        for tool in event['response_tools']:
            if tool['name']!='submit_final_items':continue
            groups=tool['input']['items'];error=None;merged=None
            try:merged=extract._merge_groups([g for g in groups if g.get('type') in extract.VALID_STATUS],candidates,apply_cap=False)
            except TypeError as exc:error=str(exc)
            malformed=[{'group':i,'members':g.get('members')} for i,g in enumerate(groups)
                if not isinstance(g.get('members'),list) or any(not isinstance(m,int) for m in g['members'])]
            replay.append({'native_attempt':event['attempt'],'stage_trace_seq':event['seq'],
                'malformed_member_groups':malformed,'offline_error':error,'merged_items':len(merged) if merged is not None else None})
    failures=[r for r in replay if r['offline_error']]
    assert len(failures)==1 and failures[0]['native_attempt']==17 and failures[0]['offline_error']=="unhashable type: 'dict'"
    assert any(r['merged_items'] for r in replay)
    original=REPO/'agent/results/s2_dev16/787d88e-v1/source/agent/src/mta/extract.py'
    assert sha(original)==sha(REPO/'agent/src/mta/extract.py')
    control=result['runtime_control'];assert control['attempted_calls']==21 and control['charged_tokens']==366880
    assert not any(a['stage'] in ('select','p2_check','agent','abstract') for a in control['attempts'])
    diagnostic={'meeting_id':'TS3006d','status':'NEW_CONSOLIDATION_FAILURE_REPRODUCED','actual_new_api_calls':0,
        'error_type':'TypeError','failure':failures[0],
        'cause':'Model supplied dict-valued candidate members. dict.fromkeys hashes them before the integer filter, aborting the meeting.',
        'extract_code_unchanged_from_787d88e':True,'not_caused_by_selection_reason_repair':True,
        'native_consolidation_responses':replay,'calls_before_failure':21,'charged_tokens':366880,
        'unknown_usage_tokens':control['unknown_usage_tokens'],'budget_exhausted':False,
        'selection_entered':False,'p2_entered':False,'saved_checkpoints':list(meta['checkpoints']),
        'scope':'New, independent consolidation defect discovered by cold coverage. No malformed member was mapped to a candidate or accepted; no source fix or online rerun performed.'}
    (root/'consolidation_failure.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':diagnostic['status'],'actual_new_api_calls':0,'error':failures[0]['offline_error'],
        'consolidation_code_unchanged':True},ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('root',type=Path);args=ap.parse_args();main(args.root.resolve())
