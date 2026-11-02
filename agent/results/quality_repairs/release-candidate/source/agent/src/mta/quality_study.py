"""Fixed extraction/raw-response development regression for item-quality changes. Gold stays offline."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import ast
import hashlib

from . import config, pipeline, support
from .baseline import check_destination, check_selection
from .control import RunControl, RunStopped
from .evaluate import make_llm_matcher
from .judge import JsonCache
from .llm import LLM
from .observation import REPOSITORY, source_files, runtime_identity, sha256, utc_now, write_json
from .p2_repair import RemainingBudgetLLM
from .regression import RecordingLLM, load, route_inputs, score_items, gate_summary
from .store import Store
from .trace import StageTrace


def checkpoint(root, mid, stage):
    metadata = load(root/'after/s2'/(mid+'.run.json'))
    suffix = Path(metadata['checkpoints'][stage]).relative_to(Path(metadata['artifacts']['metadata']).parent)
    return root/'after/s2'/suffix


def prepare(root, before, selection_from=None):
    check_destination(root)
    old = load(before/'study.json')
    check_selection(old['meetings'], ['s2'])
    if runtime_identity(LLM())['model'] != old['runtime']['model']:
        raise ValueError('Keep the captured model')
    root.mkdir(parents=True)
    (root/'.gitattributes').write_text('* -text -whitespace\n', encoding='utf-8')
    for name in ('inputs','gold'):
        shutil.copytree(before/name, root/name)
    shutil.copytree(before/'after/_cache', root/'after/_cache')
    (root/'frozen_extraction').mkdir()
    before_hashes = {str(before/'study.json'):sha256(before/'study.json')}
    for mid in old['meetings']:
        source = checkpoint(before,mid,'extracted')
        shutil.copyfile(source,root/'frozen_extraction'/(mid+'.json'))
        for p in (source,before/'after/s2'/(mid+'.json'),before/'after/s2'/(mid+'.p2.json')):
            before_hashes[str(p)] = sha256(p)
    for path in source_files()+[config.SCHEMA, config.P2_SCHEMA]:
        target = root/'source'/path.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    debit=copy.deepcopy(old['upstream'])
    seed_info={}
    if selection_from:
        # Reuse the FIRST raw selection response, before looking at any scores. Inputs, model,
        # system prompt and wire schema must be identical; only deterministic interpretation changes.
        from .item_selection import SYSTEM
        def node(path,name):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            return next(n for n in tree.body if isinstance(n,(ast.Assign,ast.FunctionDef)) and
                        (getattr(n,'name',None)==name or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)))
        prior_source=selection_from/'source/agent/src/mta/item_selection.py'
        actual=REPOSITORY/'agent/src/mta/item_selection.py'
        for name in ('SYSTEM','review_schema'):
            if ast.dump(node(prior_source,name),include_attributes=False)!=ast.dump(node(actual,name),include_attributes=False):
                raise ValueError('Selection prompt/wire changed; cannot reuse responses')
        if load(selection_from/'study.json')['runtime']['model']!=old['runtime']['model']:
            raise ValueError('Selection model changed')
        for mid in old['meetings']:
            events=[json.loads(s) for s in (selection_from/'after/s2'/(mid+'.stages.jsonl')).read_text(encoding='utf-8').splitlines()]
            attempt=next(e for e in events if e['stage']=='select' and e['event']=='attempt')
            user=attempt['input']
            if 'correction' in user:raise ValueError('Only the first proposal can be seeded')
            key=hashlib.sha256(json.dumps([SYSTEM,config.ITEM_POLICY_VERSION,old['runtime']['model']['name'],user],sort_keys=True).encode()).hexdigest()
            prior_result=load(selection_from/'after'/(mid+'.result.json'))
            charged=prior_result['runtime_control']['attempts'][0]
            if charged['stage']!='select' or charged['outcome']!='completed':raise ValueError('Need a measured first selection request')
            start=next(e['t'] for e in events if e['event']=='stage_started' and e.get('current_stage')=='select')
            extra={'attempts':1,'charged_tokens':charged['charged_tokens'],'elapsed_seconds':round(attempt['t']-start,3)}
            origin={'source':str(selection_from),'meeting':mid,'attempt':0,'raw_response_sha256':hashlib.sha256(json.dumps(attempt['raw_response'],sort_keys=True).encode()).hexdigest(),
                    'policy':'Reuse first response, not best response; same system, wire, model and request input. Debit its actual captured cost.','captured_cost':extra}
            write_json(root/'after/_cache'/(f'select_{mid}.json'),{'key':key,'response':attempt['raw_response'],'origin':origin})
            for k in ('attempts','charged_tokens','elapsed_seconds'):debit[mid][k]+=extra[k]
            debit[mid]['selection_replay']=origin
            seed_info[mid]=origin
    study = {'format_version':1,'created_at':utc_now(),'status':'PREPARED','before':str(before),
        'meetings':old['meetings'],'runtime':runtime_identity(LLM()),'upstream':debit,'selection_replay':seed_info,
        'scope':'Identical frozen extraction and real raw consolidation responses; live selection/P2/summary. Original upstream attempts, tokens and elapsed time reserved. SB development, not held-out evaluation.',
        'before_sha256':before_hashes,
        'artifact_sha256':{p.relative_to(root).as_posix():sha256(p) for folder in ('inputs','gold','source','frozen_extraction','after/_cache')
                          for p in sorted((root/folder).rglob('*')) if p.is_file()}}
    write_json(root/'study.json',study)


def verify(root):
    study = load(root/'study.json')
    for name,expected in study['artifact_sha256'].items():
        if sha256(root/name)!=expected:
            raise ValueError('Frozen input/source changed: '+name)
    for name,expected in study['before_sha256'].items():
        if sha256(Path(name))!=expected:raise ValueError('Before changed: '+name)
    for name,expected in study['runtime']['code']['source_sha256'].items():
        if sha256(REPOSITORY/name)!=expected:raise ValueError('Runtime changed: '+name)
    return study


def run(root):
    study = verify(root)
    route_inputs(config,root)
    original = pipeline.extract_candidates
    try:
        for mid in study['meetings']:
            path = root/'after'/(mid+'.result.json')
            if path.exists():raise FileExistsError('Never overwrite an attempt')
            captured = load(root/'frozen_extraction'/(mid+'.json'))
            def extraction(*args, **kwargs):
                trace = kwargs['trace']
                trace.log(stage='extract',event='frozen_candidates',source_sha256=sha256(root/'frozen_extraction'/(mid+'.json')),
                          candidates=captured,reason='Fixed-input development comparison; no extraction API call')
                return copy.deepcopy(captured)
            pipeline.extract_candidates = extraction
            trace = StageTrace(root/'after'/(mid+'.requests.jsonl'))
            debit = study['upstream'][mid]
            client = RemainingBudgetLLM(trace,debit)
            result = pipeline.run_meeting(config.TRANSCRIPTS/(mid+'.txt'),'s2',client,root/'after',root/'after/_cache')
            result['actual_new_usage'] = client.usage_snapshot()
            result['whole_meeting_budget'] = {'captured_upstream':debit,
                'combined_attempts':debit['attempts']+result['runtime_control']['attempted_calls'],
                'combined_charged_tokens':debit['charged_tokens']+result['runtime_control']['charged_tokens']}
            assert result['whole_meeting_budget']['combined_attempts']<=config.MAX_MODEL_CALLS
            assert result['whole_meeting_budget']['combined_charged_tokens']<=config.MAX_TOTAL_TOKENS
            if any(client.stages.get(s,{}).get('calls',0) for s in ('extract','consolidate','decision_consolidate')):
                result.update(status='FAILED',reason='frozen_response_cache_miss')
            if result['status']=='COMPLETED':
                metadata = load(Path(result['run_metadata']))
                assert load(Path(metadata['checkpoints']['extracted']))==captured
                for stage in ('consolidated','selected','normalized'):
                    write_json(root/'after'/(mid+'.'+stage+'.json'),load(Path(metadata['checkpoints'][stage])))
            write_json(path,result)
            trace.finish(stage='quality_capture',event='finished',status=result['status'])
            print(json.dumps({'meeting_id':mid,'status':result['status'],'budget':result['whole_meeting_budget'],
                              'reason':result.get('reason')},ensure_ascii=False),flush=True)
            if result['status']!='COMPLETED':break
    finally:
        pipeline.extract_candidates = original


def score(root):
    study = verify(root)
    route_inputs(config,root)
    before = Path(study['before'])
    spec=importlib.util.spec_from_file_location('_quality_eval_protocol',config.ROOT/'scripts/full_eval.py')
    protocol=importlib.util.module_from_spec(spec);spec.loader.exec_module(protocol)
    folder=root/'scoring';folder.mkdir(exist_ok=True)
    output=[]
    for mid in study['meetings']:
        destination=folder/(mid+'.json')
        if destination.exists():raise FileExistsError('Never overwrite scoring')
        result=root/'after'/(mid+'.result.json')
        if not result.exists() or load(result)['status']!='COMPLETED':
            value={'meeting_id':mid,'status':'UNSCORED','reason':'pipeline incomplete'}
            write_json(destination,value);output.append(value);continue
        trace=StageTrace(folder/(mid+'.requests.jsonl'))
        client=RecordingLLM(trace);control=RunControl(trace=trace)
        value={'meeting_id':mid,'status':'RUNNING','arms':{},'judge':'same model, same-type one-to-one three votes; frozen before/reference judgments reused'}
        try:
            scoped=client.scoped(control)
            cache=JsonCache.get(folder/(mid+'.align_votes.json'))
            cache.update_from(load(before/'scoring'/(mid+'.align_votes.json')))
            matcher=make_llm_matcher(scoped,cache=cache,votes=3)
            gold=load(root/'gold'/(mid+'.json'))
            canonical=load(config.ROOT/'results/p2_repairs/repair-v1/scoring'/(mid+'.json'))
            value['arms']['teammate']=copy.deepcopy(canonical['arms']['teammate'])
            value['arms']['before']=copy.deepcopy(load(before/'scoring'/(mid+'.json'))['arms']['after'])
            record=load(root/'after/s2'/(mid+'.json'))
            value['arms']['after']=score_items(record,gold,matcher,protocol.FEWSHOT_GOLD)
            write_json(destination,value)
            normalized=load(root/'after'/(mid+'.normalized.json'))
            value['arms']['before_p2']=score_items([{'id':f'{mid}.pre.{i}',**x} for i,x in enumerate(normalized)],gold,matcher,protocol.FEWSHOT_GOLD)
            value['p2']=gate_summary(load(root/'after/s2'/(mid+'.p2.json')))
            store=Store(mid,allow_prior=True,run_dir=root/'after/s2')
            items=[x for x in record if x['type']!='abstract']
            verdicts=support.judge_meeting(scoped,store,items,JsonCache.get(folder/(mid+'.support.json'))) if items else []
            value['support']=[{'id':i['id'],'text':i['text'],'verdict':v} for i,v in zip(items,verdicts)]
            value['status']='COMPLETED'
        except RunStopped as exc:
            value.update(status=exc.failure['status'],failure=exc.failure)
        except Exception as exc:
            value.update(status='FAILED',error_type=type(exc).__name__)
        finally:
            control.seal();value.update(runtime_control=control.snapshot(),actual_new_usage=client.usage_snapshot())
            trace.finish(stage='quality_score',event='finished',status=value['status']);write_json(destination,value)
        output.append(value)
        print(json.dumps({'meeting_id':mid,'scoring_status':value['status']},ensure_ascii=False),flush=True)
    write_json(root/'comparison.json',{'scope':study['scope'],'meetings':output})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['prepare','run','score']);p.add_argument('destination',type=Path)
    p.add_argument('--before',type=Path,default=config.ROOT/'results/p2_repairs/repair-v3')
    p.add_argument('--selection-from',type=Path,help='Replay first real selection responses with verified same request; reserve their measured costs')
    args=p.parse_args();root=args.destination.resolve()
    if args.operation=='prepare':prepare(root,args.before.resolve(),args.selection_from.resolve() if args.selection_from else None)
    elif args.operation=='run':run(root)
    else:score(root)
