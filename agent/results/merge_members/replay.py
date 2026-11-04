"""Replay each actual D response separately; do not invent unavailable retry responses."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
OLD=REPO/'agent/results/select_reason/ts3006-v1'
sys.path.insert(0,str(REPO/'agent/src'))
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    from mta import extract as current
    from mta.trace import StageTrace
    spec=importlib.util.spec_from_file_location('mta._extract_before_members',OLD/'source/agent/src/mta/extract.py')
    before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
    fixture=REPO/'agent/tests/fixtures/consolidation_bad_members.json';data=load(fixture)
    assert sha(fixture)==load(ROOT/'capture.json')['fixture_sha256']
    assert before.CONSOLIDATE_SCHEMA==current.CONSOLIDATE_SCHEMA
    assert before.CONSOLIDATE_SYSTEM==current.CONSOLIDATE_SYSTEM and before.DECISION_CONSOLIDATE_SYSTEM==current.DECISION_CONSOLIDATE_SYSTEM
    rows=[]
    for response in data['responses']:
        raw=copy.deepcopy(response['raw_response']);old_out=None;old_error=None
        try:old_out=before._merge_groups(raw['items'],data['candidates'],apply_cap=False)
        except TypeError as error:old_error=str(error)
        trace=StageTrace(ROOT/(str(response['attempt'])+'.merge.trace.jsonl'))
        new_out=current._merge_groups(raw['items'],data['candidates'],trace,apply_cap=False)
        assert raw==response['raw_response']
        if response['attempt']==17:
            assert old_error=="unhashable type: 'dict'" and new_out==[]
            assert [r['group_index'] for r in trace.events[-1]['rejected'] if r['reason']=='members_must_be_candidate_indexes']==[9,11]
        else:assert old_error is None and new_out==old_out
        rows.append({'native_attempt':response['attempt'],'before_error':old_error,
            'before_output':old_out,'after_output':new_out,'rejected':trace.events[-1]['rejected']})
    report={'result':'PASSED','actual_new_api_calls':0,'fixture_sha256':sha(fixture),
        'before_source_sha256':sha(OLD/'source/agent/src/mta/extract.py'),
        'after_source_sha256':sha(REPO/'agent/src/mta/extract.py'),
        'initial_prompts_and_schema_unchanged':True,'responses':rows,
        'interpretation':'Bad sample 17 is explicitly rejected without mapping participant objects to candidate numbers. Valid sample 18 is unchanged, and empty sample 19 stays unusable. These are separate real responses, not a synthetic successful end-to-end replay. Existing retry/peer preservation and budget boundaries are checked by unit tests; no unavailable live response is authored.'}
    (ROOT/'replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'result':'PASSED','actual_new_api_calls':0,'bad_sample_rejected_without_exception':True,
        'valid_peer_unchanged':True,'empty_peer_not_accepted':True}))

if __name__=='__main__':main()
