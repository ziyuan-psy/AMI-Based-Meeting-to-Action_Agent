"""Replay the two actual selection rejections. No new model calls or proposed fix."""
import argparse
import json
from pathlib import Path
import sys

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
sys.path.insert(0,str(REPO/'agent/src'))
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def main(root):
    from mta.item_selection import canonical_reviews
    mid='TS3006b';out=root/'run/s2';meta=load(out/(mid+'.run.json'))
    events=[json.loads(line) for line in (out/(mid+'.stages.jsonl')).read_text(encoding='utf-8').splitlines() if line.strip()]
    pool=load(Path(meta['checkpoints']['consolidated']));attempts=[]
    for event in events:
        if event.get('stage')!='select' or event.get('event')!='attempt':continue
        actual=None
        try:canonical_reviews(event['raw_response'],pool,[])
        except ValueError as error:actual=str(error)
        assert actual==event['error'] and actual=="C10: 'reason' is a required property"
        missing=[{'source':key,'keep':value.get('keep'),'duplicate_of':value.get('duplicate_of')}
            for key,value in event['raw_response'].items() if 'reason' not in value]
        attempts.append({'native_attempt':event['attempt'],'stage_trace_seq':event['seq'],
            'recorded_error':event['error'],'offline_reproduced_error':actual,'missing_reason':missing})
    result=load(root/'run'/(mid+'.result.json'));control=result['runtime_control']
    assert len(attempts)==2 and control['attempted_calls']==20 and control['charged_tokens']==309656
    assert not any(a['stage'] in ('p2_check','agent','abstract') for a in control['attempts'])
    assert control['charged_tokens']<control['limits']['tokens'] and control['attempted_calls']<control['limits']['calls']
    diagnostic={'meeting_id':mid,'status':'OFFLINE_FAILURE_REPRODUCED','actual_new_api_calls':0,
        'failed_stage':'select','error_type':'ValueError','cause':'Required exclusion reason omitted in both native model submissions.',
        'selection_attempts':attempts,'calls_before_failure':20,'charged_tokens_before_failure':309656,
        'p2_entered':False,'budget_exhausted':False,'new_sampling_or_source_modification':False,
        'saved_checkpoints':list(meta['checkpoints']),'partial_artifact':result['partial_artifact'],
        'blocked_downstream':['TS3006c','TS3006d'],
        'interpretation':'Same captured model output deterministically fails the frozen selection contract. One cohort cannot estimate omission frequency. No normalization or source fix applied.'}
    (root/'selection_failure.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':diagnostic['status'],'actual_new_api_calls':0,'native_attempts':len(attempts),'p2_entered':False}))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('root',type=Path);args=ap.parse_args();main(args.root.resolve())
