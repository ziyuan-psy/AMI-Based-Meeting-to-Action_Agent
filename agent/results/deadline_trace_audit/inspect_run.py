"""Inspect the saved fresh-v5 run without modifying it or calling the provider."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = next(p for p in ROOT.parents if (p / 'agent/src/mta/p2.py').exists())
BEFORE = REPO / 'agent/results/smoke_repairs/repair-v5'


def load(p):
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def rows(v):
    return v if isinstance(v, list) else v.get('items', v.get('outcomes', []))


def main():
    protection = REPO / 'agent/results/smoke_repairs/manifest.json'
    for rel, digest in load(protection)['artifact_sha256'].items():
        assert sha(protection.parent / rel) == digest, rel
    protected = load(BEFORE / 'protected_before.json')
    for rel, digest in protected.items():
        assert sha(REPO / rel) == digest, rel
    snapshot = ROOT / 'before.json'
    if not snapshot.exists():
        save(snapshot, {'base_commit': load(BEFORE / 'study.json')['base_commit'],
            'previous_repair_manifest_sha256': sha(protection),
            'protected_files': protected,
            'source_and_tests_sha256': {p.relative_to(REPO).as_posix(): sha(p)
                for folder in ('agent/src', 'agent/tests') for p in (REPO / folder).rglob('*.py')},
            'previous_repairs_sha256': load(protection)['artifact_sha256']})
    meta = load(BEFORE / 'run/s2/IS1008a.run.json')
    slides = {}
    for key, path in meta['checkpoints'].items():
        value = load(Path(path))
        matches = [r for r in rows(value) if 'slides' in r.get('text', r.get('item_text', '')).lower()]
        if matches:
            slides[key] = matches
    p2 = load(BEFORE / 'run/s2/IS1008a.p2.json')
    slides['final_p2'] = [r for r in p2['items'] if 'slides' in r['item_text'].lower()]
    ts = load(BEFORE / 'run/s2/TS3006b.p2.json')['items']
    tsmeta = load(BEFORE / 'run/s2/TS3006b.run.json')
    omitted = load(Path(tsmeta['checkpoints']['verified_omitted']))['outcomes']
    ts += omitted
    result = load(BEFORE / 'run/TS3006b.result.json')
    attempts = result['runtime_control']['attempts']
    actions = []
    outcomes = []
    repeats = defaultdict(list)
    for it in sorted(ts, key=lambda it: int(it['item_key'].split('#')[1])):
        steps = []
        for e in it['history']:
            rec = {k: e[k] for k in ('step', 'action', 'status', 'state', 'decision', 'reason',
                'args', 'new_info', 'error', 'requested_action', 'protocol_normalizations') if k in e}
            if 'tool_result' in e:
                data = e['tool_result']
                rec['tool_result'] = data
                args = e.get('args', {})
                repeats[e['action'] + ':' + json.dumps(args, sort_keys=True)].append(it['item_key'])
            if 'checks' in e:
                rec['checks'] = {k: {s: v.get(s) for s in ('result', 'reason', 'fields')} for k, v in e['checks'].items()}
                rec['proposed_fields'] = e.get('proposed_fields')
            steps.append(rec)
            actions.append(e['action'])
        outcomes.append({'key': it['item_key'], 'text': it['item_text'], 'state': it['state'],
            'recordable': it['recordable'], 'checks': it['checks'], 'steps': steps})
    save(ROOT / 'before_audit.json', {'slides': slides, 'ts3006b': {
        'stage_usage': result['stages'], 'runtime_control': result['runtime_control'],
        'action_counts': dict(Counter(actions)), 'outcomes': outcomes,
        'repeated_lookups': {k: v for k, v in repeats.items() if len(v) > 1}}})
    print(json.dumps({'slides_checkpoints': {k:[{s:r.get(s) for s in ('text','deadline','deadline_basis','evidence_da_ids')}
        for r in v] for k,v in slides.items() if k in ('extracted','consolidated','selected','triaged')},
        'ts3006b': {'usage': result['stages'], 'actions': dict(Counter(actions)),
            'attempt_example': attempts[0],
            'items': [{'key': it['key'], 'text': it['text'], 'state': it['state'],
                'steps': [{k: v for k, v in s.items() if k not in ('tool_result','checks','proposed_fields')}
                          for s in it['steps']]} for it in outcomes]}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
