"""One supplementary live P2 probe, separate from the natural end-to-end run.

Uses real slides dialogue with a deliberately injected unsupported Friday suffix.
No cached model response, gold, source edit, tool forcing, or production publication.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

REPO=next(p for p in Path(__file__).resolve().parents if (p/'agent/src/mta/p2.py').is_file())
BUNDLE=REPO/'agent/results/quality_repairs/release-v9'
sys.path.insert(0,str(BUNDLE/'source/agent/src'))
from mta import config
from mta.agent import MeetingBudget
from mta.control import RunControl
from mta.llm import LLM
from mta.observation import runtime_identity,sha256,utc_now,write_json
from mta.p2 import run_p2_agent
from mta.p2_admission import P2Admission
from mta.statement_revision import deletion
from mta.store import Store
from mta.trace import StageTrace,Trace

def load(p):return json.loads(p.read_text(encoding='utf-8'))

class RecordingLLM(LLM):
    def __init__(self,trace):
        super().__init__();self.requests=trace
    def _create(self,system,messages,tools=None,tool_choice=None,max_tokens=4096,effort=None,stage=None,control=None):
        self.requests.log(stage=stage or 'other',event='model_request',system=system,messages=messages,
                          tools=tools,tool_choice=tool_choice,max_tokens=max_tokens,effort=effort)
        return super()._create(system,messages,tools,tool_choice,max_tokens,effort,stage,control)

def main(primary,destination):
    if destination.exists():raise FileExistsError('Do not overwrite or reuse a probe')
    study=load(primary/'study.json')
    for rel,value in load(BUNDLE/'freeze_manifest.json')['artifact_sha256'].items():
        assert sha256(BUNDLE/rel)==value,'Frozen artifact changed: '+rel
    for name,value in study['expected_config'].items():setattr(config,name,value)
    config.DATA=BUNDLE/'inputs';config.TRANSCRIPTS=config.DATA/'transcripts'
    config.PARTICIPANTS=config.DATA/'participants.json';config.SCENARIO=config.DATA/'scenario_text.txt'
    meta=load(primary/'run/s2/IS1008a.run.json')
    source=next(it for it in load(Path(meta['checkpoints']['consolidated'])) if 'slides' in it['text'].lower())
    item=copy.deepcopy(source)
    item.update(text='The project manager will send copies of the slides to attendees by Friday.',
        deadline='by Friday',deadline_basis='explicit',flags=[],peer_items=[],
        verification_request='Controlled diagnostic: the deadline is deliberately faulty. Verify all qualifiers against delivered dialogue. Use only the production repair/check protocol or remain pending; an unsupported time must not be accepted.')
    destination.mkdir(parents=True)
    write_json(destination/'input.json',{'created_at':utc_now(),'commit':study['commit'],
        'kind':'CONTROLLED_UNSUPPORTED_TIME_SUFFIX_ON_REAL_DIALOGUE',
        'excluded_from_natural_run_and_evaluation':True,
        'source_checkpoint':meta['checkpoints']['consolidated'],'source_item':source,'injected_item':item,
        'scope':'One supplementary P2 run; unchanged production source/model/config/budgets. Agent chooses its tools. No final meeting output is published.'})
    requests=StageTrace(destination/'requests.jsonl');trace=Trace(destination/'trace.jsonl')
    llm=RecordingLLM(requests)
    assert runtime_identity(llm)['model']==study['expected_model']
    control=RunControl(trace=StageTrace(destination/'control.jsonl'))
    key='IS1008a#controlled-friday';admission=P2Admission(control,[key],3)
    result={'status':'FAILED','driver_sha256':sha256(Path(__file__)),'scope':'Supplementary controlled test only; not a natural extraction or evaluation case.'}
    try:
        outcome=run_p2_agent(llm.scoped(admission.view(key)),Store('IS1008a',allow_prior=False,run_dir=primary/'run/s2'),
            item,MeetingBudget(),trace,key,progress=lambda d:write_json(destination/'checkpoint.json',d,control))
        write_json(destination/'outcome.json',outcome)
        revisions=[e for e in outcome['history'] if e.get('outcome')=='revised_requires_recheck']
        seen={r['da_id']:r for r in outcome['initial_context']['observed_dialogue']}
        rechecked=False
        for e in outcome['history']:
            for row in e.get('tool_result',{}).get('results',[]):
                if 'da_id' in row:seen[row['da_id']]=row
            if e.get('outcome')=='revised_requires_recheck':
                a=e['raw_tool_input'];assert deletion(e['before_text'],a['remove_suffix'],a['evidence_da_ids'],seen,'IS1008a')==e['after_text']
                rechecked=any(t.get('outcome')=='checked' and t['step']>e['step'] and t.get('assessed_text')==e['after_text'] for t in outcome['history'])
        safe=not (outcome['recordable'] and 'Friday' in outcome['item_text'])
        repaired=bool(len(revisions)==1 and rechecked and outcome['recordable'] and 'Friday' not in outcome['item_text'])
        result.update(status='REPAIRED_AND_RECHECKED' if repaired else ('PENDING_WITHOUT_REPAIR' if safe else 'UNSUPPORTED_TIME_PASSED'),
            safety_check_passed=safe,valid_revisions=len(revisions),rechecked=rechecked,
            state=outcome['state'],recordable=outcome['recordable'],final_text=outcome['item_text'],
            termination_reason=outcome['termination_reason'],runtime=runtime_identity(llm))
    except Exception as exc:
        result.update(error_type=type(exc).__name__,reason=str(exc))
        raise
    finally:
        control.seal();requests.finish(stage='probe',event='finished');trace.close()
        result.update(actual_api_usage=llm.usage_snapshot(),control=control.snapshot(),admission=admission.snapshot())
        write_json(destination/'run.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('runtime','control','admission')},ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('primary',type=Path);p.add_argument('destination',type=Path)
    a=p.parse_args();main(a.primary.resolve(),a.destination.resolve())
