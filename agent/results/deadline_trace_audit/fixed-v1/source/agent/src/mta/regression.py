"""Small paired SB development regression. Historical policy, common bounded transport, fresh runs.

Gold is read by scoring only. No runtime policy, prompts or limits are tuned by this module.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from . import config, pipeline, support
from .baseline import check_destination, check_selection
from .control import RunControl, RunStopped, launch
from .evaluate import TYPES, make_llm_matcher, prf
from .judge import JsonCache
from .llm import LLM
from .observation import REPOSITORY, runtime_identity, sha256, source_files, utc_now, write_json
from .p2 import validate_outputs
from .store import Store
from .trace import StageTrace
from .validate import check

DEFAULT_MEETINGS = ['TS3006a', 'TS3006b', 'IS1008a']
BASELINE_REF = '414a31f'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + REPOSITORY.as_posix(), *args], cwd=REPOSITORY)


def historical(root):
    """Import the frozen branch under its own namespace; never switch the shared checkout."""
    name = '_mta_regression_' + sha256(root / 'study.json')[:12]
    package = root / 'source_baseline/agent/src/mta'
    spec = importlib.util.spec_from_file_location(name, package / '__init__.py', submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(name + '.pipeline'), importlib.import_module(name + '.config')


def route_inputs(settings, root):
    settings.DATA = root / 'inputs'
    settings.TRANSCRIPTS = settings.DATA / 'transcripts'
    settings.PARTICIPANTS = settings.DATA / 'participants.json'
    settings.SCENARIO = settings.DATA / 'scenario_text.txt'


def prepare(destination, meetings=None, baseline=BASELINE_REF):
    meetings = check_selection(meetings or DEFAULT_MEETINGS, ['s2'])
    # All earlier selected meetings are required, so no historical output from another arm is injected.
    revision = git('rev-parse', baseline + '^{commit}').decode().strip()
    check_destination(destination)
    destination.mkdir(parents=True)
    (destination / '.gitattributes').write_text('* -text -whitespace\n', encoding='utf-8')
    paths = git('ls-tree', '-r', '--name-only', revision, 'agent/src/mta', 'agent/schemas').decode().splitlines()
    for rel in paths:
        target = destination / 'source_baseline' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git('show', revision + ':' + rel))
    for source in source_files() + [config.SCHEMA, config.P2_SCHEMA]:
        target = destination / 'source_current' / source.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for source in [config.PARTICIPANTS, config.SCENARIO] + [config.TRANSCRIPTS / (m + '.txt') for m in meetings]:
        target = destination / 'inputs' / source.relative_to(config.DATA)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Frozen labels are deliberately outside the runtime input directory and never passed to Store/tools.
    for mid in meetings:
        target = destination / 'gold' / (mid + '.json')
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(REPOSITORY / 'revised_gold_output' / target.name, target)
    manifest = {'format_version': 1, 'created_at': utc_now(), 'status': 'PREPARED', 'meetings': meetings,
                'mode': 's2', 'baseline_commit': revision, 'current': runtime_identity(LLM()),
                'execution_kind': 'fresh_live_paired_pipelines',
                'protocol': 'Frozen historical policy and current production pipeline, common current bounded LLM transport; empty independent caches and arm-local earlier records. Same frozen SB transcript/metadata/scenario/gold, same model. One run per arm; no stability/significance claim.',
                'scope': 'Development regression only; draft gold, no held-out data and no final evaluation.',
                'input_sha256': {p.relative_to(destination).as_posix(): sha256(p) for p in sorted((destination / 'inputs').rglob('*')) if p.is_file()},
                'gold_sha256': {p.name: sha256(p) for p in sorted((destination / 'gold').glob('*.json'))},
                'source_sha256': {p.relative_to(destination).as_posix(): sha256(p) for folder in ('source_baseline', 'source_current') for p in sorted((destination / folder).rglob('*')) if p.is_file()}}
    write_json(destination / 'study.json', manifest)
    old, old_config = historical(destination)
    parity = {}
    for mid in meetings:
        text = destination / 'inputs/transcripts' / (mid + '.txt')
        assert old.parse_transcript_file(text) == pipeline.parse_transcript_file(text), 'Parser mismatch: ' + mid
        parity[mid] = True
    write_json(destination / 'parser_parity.json', parity)
    return manifest


class RecordingLLM(LLM):
    """Record requests with actual returned tools; budgets/retries remain the production implementation."""
    def __init__(self, trace, **kwargs):
        super().__init__(**kwargs)
        self.request_trace = trace

    def _create(self, system, messages, tools=None, tool_choice=None, max_tokens=4096, effort=None, stage=None, control=None):
        self.request_trace.log(stage=stage or 'other', event='model_request', system=system, messages=messages,
                               tools=tools, tool_choice=tool_choice, max_tokens=max_tokens, effort=effort)
        return super()._create(system, messages, tools, tool_choice, max_tokens, effort, stage, control)


def verify_inputs(root):
    study = load(root / 'study.json')
    for rel, expected in study['input_sha256'].items():
        if sha256(root / rel) != expected:
            raise ValueError('Frozen input changed: ' + rel)
    for name, expected in study['gold_sha256'].items():
        if sha256(root / 'gold' / name) != expected:
            raise ValueError('Frozen gold changed: ' + name)
    for rel, expected in study['source_sha256'].items():
        if sha256(root / rel) != expected:
            raise ValueError('Frozen source changed: ' + rel)
    # Current pipeline must still be the captured version, even if invoked before committing this harness.
    for rel, expected in study['current']['code']['source_sha256'].items():
        if sha256(REPOSITORY / rel) != expected:
            raise ValueError('Current runtime changed after preparation: ' + rel)
    if study['current']['config'] != runtime_identity(LLM())['config']:
        raise ValueError('Runtime settings changed after preparation')
    return study


def run_arm(root, arm):
    study = verify_inputs(root)
    engine, settings = (pipeline, config) if arm == 'current' else historical(root)
    route_inputs(settings, root)
    route_inputs(config, root)  # shared transport and scorers use identical context snapshots
    out = root / arm
    out.mkdir(exist_ok=True)
    results = []
    for mid in study['meetings']:
        result_path = out / (mid + '.result.json')
        if result_path.exists():
            raise FileExistsError('Never overwrite a regression attempt: ' + str(result_path))
        request_trace = StageTrace(out / (mid + '.requests.jsonl'))
        client = RecordingLLM(request_trace)
        start = time.monotonic()
        result = {'meeting_id': mid, 'arm': arm, 'started_at': utc_now(), 'status': 'RUNNING'}
        write_json(result_path, result)
        control = None
        try:
            if arm == 'current':
                outcome = pipeline.run_meeting(config.TRANSCRIPTS / (mid + '.txt'), 's2', client, out, cache_dir=out / '_cache')
                result.update(outcome)
                if outcome['status'] == 'COMPLETED':
                    metadata = load(Path(outcome['run_metadata']))
                    shutil.copyfile(metadata['checkpoints']['normalized'], out / (mid + '.normalized.json'))
                    store = Store(mid, allow_prior=True, run_dir=out / 's2')
                    record = load(out / 's2' / (mid + '.json'))
                    errors = check(record, store) + validate_outputs(load(out / 's2' / (mid + '.p2.json')),
                        load(out / 's2' / (mid + '.clarifications.json')), record, store)
                    if errors:
                        result.update(status='BLOCKED', reason='regression_revalidation_failed', errors=errors)
            else:
                # Historical policy is untouched. Only transport and an observer around normalize are shared.
                original = engine.normalize
                def observed_normalize(*args, **kwargs):
                    value = original(*args, **kwargs)
                    write_json(out / (mid + '.normalized.json'), copy.deepcopy(value))
                    return value
                engine.normalize = observed_normalize
                control = RunControl(trace=request_trace)
                try:
                    outcome = control.wait(launch(lambda: engine.run_meeting(config.TRANSCRIPTS / (mid + '.txt'),
                        's2', client.scoped(control), out, cache_dir=out / '_cache')))
                    control.check('regression_finalize')
                    result.update(outcome)
                finally:
                    engine.normalize = original
            result['usage'] = client.usage_snapshot()
        except RunStopped as exc:
            result.update(status=exc.failure['status'], reason=exc.failure['code'], failure=exc.failure)
        except Exception as exc:
            result.update(status='FAILED', reason='regression_stage_failed', error_type=type(exc).__name__)
        finally:
            if control is not None:
                control.seal()
                result['runtime_control'] = control.snapshot()
                result['usage'] = control.usage_snapshot()
            request_trace.finish(stage='regression', event='finished', status=result['status'])
            result.update(finished_at=utc_now(), elapsed_seconds=round(time.monotonic() - start, 3))
            if result['status'] == 'COMPLETED':
                result['record_sha256'] = sha256(out / 's2' / (mid + '.json'))
            write_json(result_path, result)
        results.append(result)
        print(json.dumps({k: result.get(k) for k in ('arm', 'meeting_id', 'status', 'reason', 'elapsed_seconds')}, ensure_ascii=False), flush=True)
        if result['status'] != 'COMPLETED':
            break  # never feed a failed earlier meeting to a later one
    return results


def reuse_baseline(root, source):
    """Preserve completed baseline calls after a harness-only repair; verify identical model inputs/policy."""
    study, old = verify_inputs(root), load(source / 'study.json')
    keys = ('meetings','mode','baseline_commit','input_sha256','gold_sha256')
    if any(study[k] != old[k] for k in keys) or study['current']['model'] != old['current']['model'] or study['current']['config'] != old['current']['config']:
        raise ValueError('Cannot reuse baseline with different inputs, model, settings or historical policy')
    for rel, expected in old['source_sha256'].items():
        if sha256(source / rel) != expected:
            raise ValueError('Baseline source snapshot changed')
    # Common transport must be byte-identical; temporary-file naming is an artifact-only change.
    for rel in ('agent/src/mta/llm.py','agent/src/mta/control.py','agent/src/mta/trace.py'):
        if study['current']['code']['source_sha256'][rel] != old['current']['code']['source_sha256'][rel]:
            raise ValueError('Shared transport changed; fresh baseline required')
    for mid in study['meetings']:
        result=load(source / 'baseline' / (mid + '.result.json'))
        if result['status'] != 'COMPLETED' or sha256(source / 'baseline/s2' / (mid + '.json')) != result['record_sha256']:
            raise ValueError('Baseline attempt is incomplete or changed')
    if (root / 'baseline').exists():
        raise FileExistsError('Baseline destination already exists')
    shutil.copytree(source / 'baseline', root / 'baseline')
    write_json(root / 'baseline_reuse.json', {'execution_kind':'reuse_of_completed_live_baseline_after_artifact_write_fix',
        'created_at':utc_now(), 'source_study':str(source), 'source_study_sha256':sha256(source / 'study.json'),
        'scope':'Original live responses/records and original timing retained; not a second live run.',
        'artifact_sha256': {p.relative_to(root).as_posix():sha256(p) for p in sorted((root / 'baseline').rglob('*')) if p.is_file()}})


def is_placeholder(item):
    return item['text'].strip().casefold().rstrip('.') in ('na', 'n/a')


def score_items(system, gold, matcher, excluded_ids=()):
    """One-to-one matching; placeholder categories and few-shot items have explicit denominators."""
    scored, matches, unmatched = {}, {}, {}
    for kind in TYPES:
        ss, gg = [r for r in system if r['type'] == kind], [r for r in gold if r['type'] == kind]
        if any(is_placeholder(g) for g in gg):
            scored[kind] = {'unscored': 'NA placeholder category'}
            continue
        pairs = matcher(ss, gg)
        if len(set(pairs.values())) != len(pairs) or not set(pairs) <= {s['id'] for s in ss} or not set(pairs.values()) <= {g['id'] for g in gg}:
            raise ValueError('Alignment must be one-to-one within the same type')
        dropped = {s for s, g in pairs.items() if g in excluded_ids}
        kept = {s: g for s, g in pairs.items() if s not in dropped}
        matches.update(kept)
        scored[kind] = prf(len(kept), len(ss) - len(dropped), sum(g['id'] not in excluded_ids for g in gg))
        unmatched[kind] = {'system': [s for s in ss if s['id'] not in pairs],
                           'gold': [g for g in gg if g['id'] not in pairs.values() and g['id'] not in excluded_ids]}
    return {'metrics': scored, 'matches': matches, 'unmatched': unmatched}


def gate_summary(p2):
    items = p2['items']
    return {'items': len(items), 'accepted': sum(i['state'] == 'accepted' for i in items),
            'pending': sum(i['state'] == 'pending' for i in items),
            'quarantined': sum(not i['recordable'] for i in items),
            'recordable_pending': sum(i['state'] == 'pending' and i['recordable'] for i in items),
            'clarification_requests': sum(len(i['clarifications']) for i in items),
            'by_termination': {reason: sum(i['termination_reason'] == reason for i in items) for reason in sorted({i['termination_reason'] for i in items})},
            'documents_observed': sum(len(i.get('observed_documents', [])) for i in items),
            'documents_retained': sum(w['retained'] for i in items for w in i.get('document_evidence', [])),
            'relations_supported': sum(w['state'] == 'supported' for i in items for w in i.get('relation_evidence', [])),
            'quarantined_items': [{'item_key': i['item_key'], 'text': i['item_text'], 'reason': i['termination_reason'],
                                  'checks': i['checks']} for i in items if not i['recordable']]}


def score(root):
    study = verify_inputs(root)
    route_inputs(config, root)
    # Keep the exact few-shot exclusion policy used by the existing evaluator.
    spec = importlib.util.spec_from_file_location('_regression_eval_protocol', config.ROOT / 'scripts/full_eval.py')
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
    folder = root / 'scoring'
    folder.mkdir(exist_ok=True)
    all_results = []
    for mid in study['meetings']:
        destination = folder / (mid + '.json')
        if destination.exists():
            raise FileExistsError('Scoring attempt already exists: ' + str(destination))
        statuses = {arm: load(root / arm / (mid + '.result.json')) if (root / arm / (mid + '.result.json')).exists() else {'status':'NOT_RUN'} for arm in ('baseline','current')}
        if any(r['status'] != 'COMPLETED' for r in statuses.values()):
            value = {'meeting_id':mid, 'status':'UNSCORED', 'reason':'incomplete paired pipeline', 'pipeline_status':{a:r['status'] for a,r in statuses.items()}}
            write_json(destination, value)
            all_results.append(value)
            continue
        trace = StageTrace(folder / (mid + '.requests.jsonl'))
        client, control = RecordingLLM(trace), RunControl(trace=trace)
        value = {'meeting_id': mid, 'status':'RUNNING', 'started_at':utc_now(), 'arms':{}, 'judge':'pipeline model, same-type one-to-one, three votes; no independent human validation'}
        write_json(destination, value)
        try:
            scoped = client.scoped(control)
            matcher = make_llm_matcher(scoped, cache=JsonCache.get(folder / (mid + '.align_votes.json')), votes=3)
            gold = load(root / 'gold' / (mid + '.json'))
            records = {arm:load(root / arm / 's2' / (mid + '.json')) for arm in ('baseline','current')}
            normalized = load(root / 'current' / (mid + '.normalized.json'))
            records['current_before_p2'] = [{'id': f'{mid}.pre.{i}', **item} for i,item in enumerate(normalized)]
            for arm, record in records.items():
                value['arms'][arm] = score_items(record, gold, matcher, protocol.FEWSHOT_GOLD)
                write_json(destination, value)  # preserve each scored arm before further requests
            value['p2'] = gate_summary(load(root / 'current/s2' / (mid + '.p2.json')))
            value['support'] = {}
            cache = JsonCache.get(folder / (mid + '.support_votes.json'))
            for arm in ('baseline','current'):
                store = Store(mid, allow_prior=True, run_dir=root / arm / 's2')
                items = [i for i in records[arm] if i['type'] != 'abstract']
                verdicts = support.judge_meeting(scoped, store, items, cache) if items else []
                value['support'][arm] = [{'id':i['id'], 'text':i['text'], 'verdict':v} for i,v in zip(items,verdicts)]
                write_json(destination, value)
            value['status'] = 'COMPLETED'
        except RunStopped as exc:
            value.update(status=exc.failure['status'], failure=exc.failure)
        except Exception as exc:
            value.update(status='FAILED', reason='regression_scoring_failed', error_type=type(exc).__name__)
        finally:
            control.seal()
            value.update(finished_at=utc_now(), runtime_control=control.snapshot(), usage=control.usage_snapshot())
            trace.finish(stage='scoring', event='finished', status=value['status'])
            write_json(destination, value)
        all_results.append(value)
        print(json.dumps({'meeting_id':mid, 'scoring_status':value['status'], 'usage':value.get('usage')}, ensure_ascii=False),flush=True)
    return summarize(root, all_results)


def summarize(root, results=None):
    results = results if results is not None else [load(p) for p in sorted((root / 'scoring').glob('*.json')) if not p.name.endswith(('votes.json',))]
    eligible = [r for r in results if r['status'] == 'COMPLETED']
    aggregate = {}
    for arm in ('baseline','current','current_before_p2'):
        aggregate[arm] = {}
        for kind in TYPES:
            rows = [r['arms'][arm]['metrics'][kind] for r in eligible if 'unscored' not in r['arms'][arm]['metrics'][kind]]
            aggregate[arm][kind] = {**prf(*(sum(row[k] for row in rows) for k in ('tp','sys','gold'))), 'meetings':len(rows)}
    result = {'scope':'Small SB development regression, model-judged draft gold; not final evaluation.',
              'eligible_meetings':[r['meeting_id'] for r in eligible], 'unscored_meetings':[r['meeting_id'] for r in results if r['status']!='COMPLETED'],
              'aggregate':aggregate, 'per_meeting':results}
    write_json(root / 'comparison.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare','baseline','reuse-baseline','current','score','summarize'])
    parser.add_argument('destination', type=Path)
    parser.add_argument('--meetings', nargs='+', default=DEFAULT_MEETINGS)
    parser.add_argument('--baseline', default=BASELINE_REF)
    parser.add_argument('--from-study', type=Path)
    args = parser.parse_args(argv)
    root = args.destination.resolve()
    if args.operation == 'prepare':
        result = prepare(root, args.meetings, args.baseline)
        print(json.dumps({'status':result['status'],'meetings':result['meetings'],'baseline':result['baseline_commit']}, ensure_ascii=False),flush=True)
    elif args.operation in ('baseline','current'):
        run_arm(root,args.operation)
    elif args.operation == 'reuse-baseline':
        if args.from_study is None:
            parser.error('--from-study is required')
        reuse_baseline(root,args.from_study.resolve())
    elif args.operation == 'score':
        score(root)
    else:
        summarize(root)


if __name__ == '__main__':
    main()
