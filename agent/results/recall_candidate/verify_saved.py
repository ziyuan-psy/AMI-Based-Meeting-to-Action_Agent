"""Verify candidate evidence using its frozen scorer; no API key or older local cohorts required."""
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / '41654ab-v2'

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

class Offline:
    def forced_tool(self, *args, **kwargs):
        raise AssertionError('A recorded vote is missing; API calls are forbidden')

def main():
    manifest = load(ROOT / 'manifest.json')['artifact_sha256']
    for name, digest in manifest.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    package = ROOT / 'source/agent/src/mta'
    name = '_frozen_recall_candidate'
    spec = importlib.util.spec_from_file_location(name, package / '__init__.py', submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    evaluator = importlib.import_module(name + '.evaluate')
    regression = importlib.import_module(name + '.regression')
    protocol = load(ROOT / 'scoring_protocol.json')
    rows = []
    for mid in load(ROOT / 'study.json')['meetings']:
        folder = ROOT / 'replay' / mid
        stable = evaluator.make_llm_matcher(Offline(), cache=load(folder / 'stable_votes.json'), votes=3)
        issues = evaluator.make_llm_matcher(Offline(), cache=load(folder / 'issue_votes.json'), votes=3)
        matcher = lambda ss, gg: (issues if ss and ss[0]['type'] == 'problems' else stable)(ss, gg)
        score = regression.score_items(load(folder / 'record.json'), load(folder / 'gold.json'), matcher,
                                       protocol['fewshot_excluded_ids'])
        assert score == load(folder / 'score.json'), mid
        rows.append(score)
    expected = load(ROOT / 'comparison.json')['aggregate']
    for typ in evaluator.TYPES:
        values = [r['metrics'][typ] for r in rows if 'unscored' not in r['metrics'][typ]]
        actual = evaluator.prf(*(sum(v[k] for v in values) for k in ('tp', 'sys', 'gold')))
        assert actual == expected[typ], typ
    assert evaluator.prf(*(sum(expected[t][k] for t in evaluator.TYPES) for k in ('tp', 'sys', 'gold'))) == expected['pooled']
    print(json.dumps({'status': 'PASSED', 'artifacts_checked': len(manifest),
                      'scored_meetings_replayed': len(rows), 'pooled_f1': expected['pooled']['f1'], 'api_calls': 0}))

if __name__ == '__main__':
    main()
