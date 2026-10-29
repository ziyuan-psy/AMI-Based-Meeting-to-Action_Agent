"""Offline postflight integrity checks for the saved repair experiments; no API calls."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
REPO = next(p for p in ROOT.parents if (p / 'agent/src/mta/p2.py').is_file())
FINAL = ROOT / 'repair-v5'
BEFORE = REPO / 'agent/results/frozen_smoke'
RELEASE = REPO / 'agent/results/quality_repairs/release-v9'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_hashes(base, mapping):
    for rel, digest in mapping.items():
        assert sha(base / rel) == digest, f'Hash changed: {rel}'
    return len(mapping)


def files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and p.suffix != '.pyc')


def git(*args):
    return subprocess.check_output(
        ['git', '-c', f'safe.directory={REPO.as_posix()}', *args],
        cwd=REPO, text=True, encoding='utf-8').strip()


def main():
    assert git('branch', '--show-current') == 'agent-development'
    assert git('rev-parse', 'HEAD') == '3b886f6c0c1520a1c5bc8628da7198a871c25495'
    assert not git('diff', '--cached', '--name-only'), 'Unexpected staged changes'
    protected = verify_hashes(REPO, load(BEFORE / 'fresh-v1/protected_before.json'))
    old_smoke = verify_hashes(BEFORE, load(BEFORE / 'manifest.json')['artifact_sha256'])
    release = verify_hashes(RELEASE, load(RELEASE / 'freeze_manifest.json')['artifact_sha256'])
    snapshots = {}
    for root in sorted(ROOT.glob('repair-v*')):
        study = load(root / 'study.json')
        snapshots[root.name] = verify_hashes(root, study['artifact_sha256'])
        assert sha(BEFORE / 'manifest.json') == study['before_manifest_sha256']
    final = load(FINAL / 'study.json')
    source = final['runtime']['code']['source_sha256']
    verify_hashes(REPO, source)
    verify_hashes(FINAL / 'source', source)
    tests = load(FINAL / 'tests.json')
    assert tests['passed'] == 319 and tests['exit_code'] == 0
    assert tests['source_sha256'] == source
    test_count = verify_hashes(REPO, tests['test_sha256'])
    for rel, digest in final['artifact_sha256'].items():
        if rel.startswith('source/'):
            assert sha(REPO / rel.removeprefix('source/')) == digest
    old_config = load(BEFORE / 'fresh-v1/run/s2/IS1008a.run.json')['config']
    config = final['runtime']['config']
    config_changes = {k: {'before': old_config.get(k), 'after': config.get(k)}
                      for k in sorted(old_config.keys() | config.keys())
                      if old_config.get(k) != config.get(k)}
    assert config_changes == {
        'ITEM_POLICY_VERSION': {'before': 5, 'after': 8},
        'P2_PROTOCOL_VERSION': {'before': 6, 'after': 9}}, config_changes
    summary = load(FINAL / 'summary.json')
    assert summary['result'] == 'PASSED' and all(summary['checks'].values())
    assert all(r['status'] == 'COMPLETED' for r in summary['meetings'])
    assert not summary['gold_scored']
    counts = {'json': 0, 'jsonl': 0, 'jsonl_rows': 0, 'python': 0}
    secret = os.environ.get('OPENAI_API_KEY', '')
    assert len(secret) >= 12, 'Expected inherited credential for non-disclosing scan'
    needle = secret.encode('utf-8')
    for path in files():
        assert needle not in path.read_bytes(), f'Credential found in {path.relative_to(ROOT)}'
        if path.suffix == '.json':
            load(path)
            counts['json'] += 1
        elif path.suffix == '.jsonl':
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    json.loads(line)
                    counts['jsonl_rows'] += 1
            counts['jsonl'] += 1
        elif path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            counts['python'] += 1
    changed = git('diff', '--name-only').splitlines()
    allowed = {f'agent/src/mta/{n}.py' for n in [
        'assessment_protocol', 'config', 'extract', 'item_selection', 'p2',
        'pipeline', 'prompts', 'statement_revision']}
    allowed |= {'agent/tests/test_decision_audit.py', 'agent/tests/test_item_quality.py',
                'revised_gold_output/README.md'}
    assert set(changed) == allowed, changed
    subprocess.run(['git', '-c', f'safe.directory={REPO.as_posix()}', 'diff', '--check',
                    '--', 'agent/src', 'agent/tests'], cwd=REPO, check=True,
                   capture_output=True)
    report = {
        'result': 'PASSED', 'verified_at': datetime.now(timezone.utc).isoformat(),
        'branch': 'agent-development', 'base_commit': git('rev-parse', 'HEAD'),
        'committed': False, 'staged_files': [], 'configuration_changes': config_changes,
        'source_files_verified': len(source), 'test_files_verified': test_count,
        'unit_tests': {'passed': tests['passed'], 'seconds': tests['elapsed_seconds'],
                       'provider_calls': 0},
        'final_main_run': summary['totals'], 'functional_checks': summary['checks'],
        'controlled_probe': summary['controlled_probe'],
        'protected_files_unchanged': protected, 'previous_smoke_files_unchanged': old_smoke,
        'previous_release_files_unchanged': release,
        'experiment_snapshot_files_verified': snapshots, 'artifact_parse_checks': counts,
        'credential_scan': {'inherited_key_present': True, 'matches': 0, 'key_disclosed': False},
        'tracked_changes': changed,
        'preexisting_tracked_annotation_change': 'revised_gold_output/README.md',
        'gold_scored': False,
        'limitations': ['Single fresh main run; no F1 or stability assessment.',
                        'Main run slower than baseline.',
                        'Controlled revision probe is independent of natural extraction.',
                        'New continues case has no new human sign-off.']}
    (ROOT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)
                                            + '\n', encoding='utf-8')
    mapping = {p.relative_to(ROOT).as_posix(): sha(p) for p in files()
               if p != ROOT / 'manifest.json'}
    manifest = {'format_version': 1, 'description':
                'SHA-256 of saved repair experiments; excludes bytecode and this manifest.',
                'artifact_sha256': mapping}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)
                                       + '\n', encoding='utf-8')
    verify_hashes(ROOT, load(ROOT / 'manifest.json')['artifact_sha256'])
    print(json.dumps({'result': 'PASSED', 'artifacts': len(mapping),
                      'protected_files': protected, 'prior_smoke_files': old_smoke,
                      'prior_release_files': release, 'parsed': counts,
                      'unit_tests': tests['passed'], 'credential_matches': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
