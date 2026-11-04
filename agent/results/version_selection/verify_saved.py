"""Read-only, zero-API verification of the evidence included in this commit."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / 'agent/src/mta/evaluate.py').is_file())
ROOT = HERE / '213ccfc-v1'

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

class Offline:
    def forced_tool(self, *args, **kwargs):
        raise AssertionError('A saved vote is missing; API requests are forbidden')

def main():
    scope = load(HERE / 'COMMIT_SCOPE.json')
    files = scope['file_sha256']
    for name, digest in files.items():
        assert sha(REPO / name) == digest, name
    local_only = set(scope['local_only_excluded'])
    for name, digest in load(ROOT / 'manifest.json')['artifact_sha256'].items():
        path = ROOT / name
        relative = path.relative_to(REPO).as_posix()
        if relative in local_only and not path.exists():
            continue
        assert sha(path) == digest, name
        assert relative in files or relative in local_only, relative
    json_count = jsonl_count = 0
    for name in files:
        path = REPO / name
        if path.suffix == '.json':
            load(path)
            json_count += 1
        elif path.suffix == '.jsonl':
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    json.loads(line)
            jsonl_count += 1
    protocol = load(ROOT / 'scoring_protocol.json')
    for name, digest in protocol['scoring_source_sha256'].items():
        assert sha(REPO / name) == digest, 'Scoring implementation changed: ' + name
    sys.path.insert(0, str(REPO / 'agent/src'))
    from mta.evaluate import make_llm_matcher
    from mta.regression import score_items
    views = 0
    for mid, folder in load(ROOT / 'scoring_locations.json').items():
        directory = ROOT / folder
        inputs = load(directory / 'inputs.json')
        result = load(directory / 'result.json')
        matcher = make_llm_matcher(Offline(), cache=load(directory / 'align_votes.json'), votes=3)
        for arm, expected in result['arms'].items():
            actual = score_items(inputs['records'][arm], inputs['gold'], matcher,
                                 protocol['fewshot_excluded_ids'])
            assert actual == expected, (mid, arm)
            views += 1
        directory = ROOT / 'stable_retention' / mid
        inputs = load(directory / 'inputs.json')
        matcher = make_llm_matcher(Offline(), cache=load(directory / 'align_votes.json'), votes=3)
        assert score_items(inputs['record'], inputs['gold'], matcher,
                           protocol['fewshot_excluded_ids']) == load(directory / 'result.json')['score']
        views += 1
    assert views == load(ROOT / 'verification.json')['offline_score_views_replayed'] == 107
    print(json.dumps({'status': 'PASSED', 'inventoried_files': len(files),
                      'scored_views_replayed': views, 'api_calls': 0,
                      'json_parsed': json_count, 'jsonl_parsed': jsonl_count,
                      'local_only_files_excluded': len(local_only)}))

if __name__ == '__main__':
    main()
