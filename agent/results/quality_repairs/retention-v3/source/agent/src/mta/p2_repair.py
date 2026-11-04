"""Controlled P2 repair study: freeze upstream inputs and debit their saved cost; never read gold at runtime."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import shutil

from . import config, pipeline, support
from .baseline import check_destination, check_selection
from .control import RunControl, RunStopped
from .evaluate import make_llm_matcher
from .judge import JsonCache
from .llm import LLM
from .observation import REPOSITORY, runtime_identity, sha256, source_files, utc_now, write_json
from .regression import RecordingLLM, load, route_inputs, score_items, gate_summary
from .trace import StageTrace


class RemainingBudgetLLM(RecordingLLM):
    """A cached upstream stage reserves its original budget instead of giving P2 a free 80 calls."""
    def __init__(self, trace, debit, **kwargs):
        super().__init__(trace,**kwargs)
        self.debit, self.adjusted = debit, False

    def scoped(self, control):
        if type(control) is RunControl and not self.adjusted:
            self.adjusted = True
            original = control.limits
            control.limits = replace(original,calls=max(0,original.calls-self.debit['attempts']),
                tokens=max(0,original.tokens-self.debit['charged_tokens']),
                meeting_seconds=max(0.001,original.meeting_seconds-self.debit['elapsed_seconds']))
            control.deadline = control.started + control.limits.meeting_seconds
            control._event('captured_upstream_budget_reserved', captured=self.debit,
                           original_limits=asdict(original),remaining_limits=asdict(control.limits),
                           scope='Reservation only: these are original captured calls, not new provider calls.')
        return super().scoped(control)


def before_normalized_path(source, mid):
    exported = source/'current'/(mid+'.normalized.json')
    if exported.is_file():
        return exported
    metadata = load(source/'current/s2'/(mid+'.run.json'))
    # A failed run still has native checkpoints. Rebase the saved relative suffix so captures also
    # remain inspectable after moving the repository, rather than writing a missing old convenience file.
    suffix = Path(metadata['checkpoints']['normalized']).relative_to(Path(metadata['artifacts']['metadata']).parent)
    return source/'current/s2'/suffix


def prepare(root, source, fresh=False):
    check_destination(root)
    original=load(source/'study.json')
    check_selection(original['meetings'],['s2'])
    for rel,expected in original['input_sha256'].items():
        if sha256(source/rel)!=expected:raise ValueError('Source input changed')
    for name,expected in original['gold_sha256'].items():
        if sha256(source/'gold'/name)!=expected:raise ValueError('Source gold changed')
    runtime=runtime_identity(LLM())
    if original['current']['model']!=runtime['model']:
        raise ValueError('Use the same model as the captured before run')
    for key,value in original['current']['config'].items():
        if key not in ('P2_PROTOCOL_VERSION','P2_RESERVED_COMPLETION_CALLS') and runtime['config'].get(key)!=value:
            raise ValueError('Unrelated runtime setting changed: '+key)
    root.mkdir(parents=True)
    (root/'.gitattributes').write_text('* -text -whitespace\n',encoding='utf-8')
    shutil.copytree(source/'inputs',root/'inputs')
    shutil.copytree(source/'gold',root/'gold')
    if not fresh:
        shutil.copytree(source/'current/_cache',root/'after/_cache')
    upstream={}
    for mid in original['meetings']:
        metadata=load(source/'current/s2'/(mid+'.run.json'))
        attempts=[r for r in metadata['runtime_control']['attempts'] if r['stage'] in ('extract','consolidate','decision_consolidate')]
        events=[json.loads(line) for line in (source/'current/s2'/(mid+'.stages.jsonl')).read_text(encoding='utf-8').splitlines()]
        start=next(e['t'] for e in events if e['stage']=='run' and e['event']=='started')
        end=next(e['t'] for e in events if e['stage']=='normalize' and e['event']=='completed')
        upstream[mid]={'attempts':len(attempts),'charged_tokens':sum(r['charged_tokens'] for r in attempts),
                       'elapsed_seconds':round(end-start,3),'source_metadata_sha256':sha256(source/'current/s2'/(mid+'.run.json')),
                       'normalized_sha256':sha256(before_normalized_path(source,mid))}
    for path in source_files()+[config.SCHEMA,config.P2_SCHEMA]:
        target=root/'source'/path.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    study={'format_version':1,'created_at':utc_now(),'status':'PREPARED','source_study':str(source),
        'source_study_sha256':sha256(source/'study.json'),'meetings':original['meetings'],
        'execution_kind':'fresh_full_pipeline' if fresh else 'cached_upstream_live_p2_and_summary_with_original_budget_reserved',
        'fresh':fresh,'upstream':upstream,'runtime':runtime_identity(LLM()),
        'scope':'SB development repair check, same frozen draft gold; no held-out evaluation or independent human review.',
        'input_sha256':{p.relative_to(root).as_posix():sha256(p) for folder in ['inputs','gold'] for p in sorted((root/folder).rglob('*')) if p.is_file()},
        'source_sha256':{p.relative_to(root).as_posix():sha256(p) for p in sorted((root/'source').rglob('*')) if p.is_file()}}
    write_json(root/'study.json',study)
    return study


def verify(root):
    study=load(root/'study.json')
    for rel,expected in {**study['input_sha256'],**study['source_sha256']}.items():
        if sha256(root/rel)!=expected:raise ValueError('Study input/source changed: '+rel)
    for rel,expected in study['runtime']['code']['source_sha256'].items():
        if sha256(REPOSITORY/rel)!=expected:raise ValueError('Runtime changed: '+rel)
    source=Path(study['source_study'])
    if sha256(source/'study.json')!=study['source_study_sha256']:raise ValueError('Before study changed')
    return study


def run(root):
    study=verify(root)
    route_inputs(config,root)
    source=Path(study['source_study'])
    out=root/'after';out.mkdir(exist_ok=True)
    for mid in study['meetings']:
        destination=out/(mid+'.result.json')
        if destination.exists():raise FileExistsError('Never overwrite a captured attempt')
        trace=StageTrace(out/(mid+'.requests.jsonl'))
        debit=study['upstream'][mid]
        client=RecordingLLM(trace) if study['fresh'] else RemainingBudgetLLM(trace,debit)
        result=pipeline.run_meeting(config.TRANSCRIPTS/(mid+'.txt'),'s2',client,out,cache_dir=out/'_cache')
        result['actual_new_usage']=client.usage_snapshot()
        if result['status']=='COMPLETED':
            metadata=load(Path(result['run_metadata']))
            normalized=load(Path(metadata['checkpoints']['normalized']))
            write_json(out/(mid+'.normalized.json'),normalized)
            result['normalized_identical_to_before']=normalized==load(before_normalized_path(source,mid))
            if not study['fresh']:
                if not result['normalized_identical_to_before'] or any(client.stages.get(s,{}).get('calls',0) for s in ('extract','consolidate','decision_consolidate')):
                    result.update(status='FAILED',reason='controlled_upstream_mismatch')
        if not study['fresh']:
            control=result['runtime_control']
            result['whole_meeting_budget']={'original_limits':{'calls':config.MAX_MODEL_CALLS,'tokens':config.MAX_TOTAL_TOKENS,'seconds':config.MEETING_TIMEOUT},
                'captured_upstream':debit,'new_attempts':control['attempted_calls'],'new_charged_tokens':control['charged_tokens'],
                'combined_attempts':debit['attempts']+control['attempted_calls'],
                'combined_charged_tokens':debit['charged_tokens']+control['charged_tokens']}
            assert result['whole_meeting_budget']['combined_attempts']<=config.MAX_MODEL_CALLS
            assert result['whole_meeting_budget']['combined_charged_tokens']<=config.MAX_TOTAL_TOKENS
        write_json(destination,result)
        trace.finish(stage='repair_capture',event='finished',status=result['status'])
        print(json.dumps({'meeting_id':mid,'status':result['status'],'new_calls':result['runtime_control']['attempted_calls'],
                          'combined_calls':result.get('whole_meeting_budget',{}).get('combined_attempts'),
                          'reason':result.get('reason')},ensure_ascii=False),flush=True)
        if result['status']!='COMPLETED':break


def score(root):
    study=verify(root);route_inputs(config,root)
    source=Path(study['source_study'])
    spec=importlib.util.spec_from_file_location('_p2_repair_protocol',config.ROOT/'scripts/full_eval.py')
    protocol=importlib.util.module_from_spec(spec);spec.loader.exec_module(protocol)
    folder=root/'scoring';folder.mkdir(exist_ok=True)
    output=[]
    for mid in study['meetings']:
        destination=folder/(mid+'.json')
        if destination.exists():raise FileExistsError('Do not overwrite scoring')
        result_path=root/'after'/(mid+'.result.json')
        if not result_path.exists() or load(result_path)['status']!='COMPLETED':
            value={'meeting_id':mid,'status':'UNSCORED','reason':'after pipeline incomplete'}
            write_json(destination,value);output.append(value);continue
        cache_path=folder/(mid+'.align_votes.json')
        prior_cache=source/'scoring'/cache_path.name
        cache=JsonCache.get(cache_path)
        if prior_cache.exists():cache.update_from(load(prior_cache))
        trace=StageTrace(folder/(mid+'.requests.jsonl'))
        client=RecordingLLM(trace);control=RunControl(trace=trace)
        value={'meeting_id':mid,'status':'RUNNING','arms':{},'judge':'same pipeline model, same-type one-to-one majority of three votes'}
        try:
            scoped=client.scoped(control);matcher=make_llm_matcher(scoped,cache=cache,votes=3)
            gold=load(root/'gold'/(mid+'.json'))
            records={'teammate':load(source/'baseline/s2'/(mid+'.json')),'after':load(root/'after/s2'/(mid+'.json'))}
            before=source/'current/s2'/(mid+'.json')
            if load(source/'current'/(mid+'.result.json'))['status']=='COMPLETED':records['before']=load(before)
            for arm,record in records.items():
                value['arms'][arm]=score_items(record,gold,matcher,protocol.FEWSHOT_GOLD)
                write_json(destination,value)
            value['p2']=gate_summary(load(root/'after/s2'/(mid+'.p2.json')))
            from .store import Store
            store=Store(mid,allow_prior=True,run_dir=root/'after/s2')
            items=[r for r in records['after'] if r['type']!='abstract']
            verdicts=support.judge_meeting(scoped,store,items,JsonCache.get(folder/(mid+'.support.json'))) if items else []
            value['support']=[{'id':it['id'],'text':it['text'],'verdict':v} for it,v in zip(items,verdicts)]
            value['status']='COMPLETED'
        except RunStopped as exc:
            value.update(status=exc.failure['status'],failure=exc.failure)
        except Exception as exc:
            value.update(status='FAILED',error_type=type(exc).__name__)
        finally:
            control.seal();value.update(runtime_control=control.snapshot(),actual_new_usage=client.usage_snapshot())
            trace.finish(stage='repair_score',event='finished',status=value['status'])
            write_json(destination,value)
        output.append(value)
        print(json.dumps({'meeting_id':mid,'scoring_status':value['status']},ensure_ascii=False),flush=True)
    write_json(root/'comparison.json',{'scope':study['execution_kind'],'meetings':output})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['prepare','run','score'])
    parser.add_argument('destination',type=Path)
    parser.add_argument('--source',type=Path,default=config.ROOT/'results/development_regression/regression-v2')
    parser.add_argument('--fresh',action='store_true')
    args=parser.parse_args(argv);root=args.destination.resolve()
    if args.operation=='prepare':prepare(root,args.source.resolve(),args.fresh)
    elif args.operation=='run':run(root)
    else:score(root)


if __name__=='__main__':main()
