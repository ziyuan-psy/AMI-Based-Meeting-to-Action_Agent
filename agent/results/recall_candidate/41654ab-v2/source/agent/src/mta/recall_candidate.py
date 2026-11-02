"""414 content policy with type-aware Action and original Issue hypothesis recovery.

Uses current transport, format guards, bounded workers and atomic cache writes.
Raw hypotheses remain provisional; this profile has no full P2 publication gate.
"""
from __future__ import annotations

import hashlib
import json
from . import config, extract, baseline_prompts
from .control import stage_pool
from .observation import write_json
from .trace import StageTrace


def extract_candidates(llm, acts, roster, **kwargs):
    return extract.extract_candidates(llm, acts, roster, system_prompt=baseline_prompts.EXTRACT_SYSTEM, **kwargs)


def finish_samples(runs, cands, trace=None):
    """The same deterministic composition used in the 16-meeting replay."""
    if not runs:
        raise RuntimeError('consolidation produced no usable samples')
    voted = extract.vote_items(runs, trace, refine_decisions=False) if len(runs) > 1 else runs[0]
    if len(runs) == 1:
        extract._audit(trace, 'consolidate', 'vote_skipped', reason='one_usable_sample', kept=voted)
    actions = extract.restore_actions(voted, cands, trace, apply_cap=False)
    # 414 cached items had no candidate_indexes. Reproduce that tested recovery rule
    # even when the current merger supplies lineage: preserve lineage in trace, but
    # restore original hypotheses unless text AND evidence exactly duplicate a kept
    # Problem. A merged source index alone is not proof its original claim survived.
    expanded = extract.restore_problems(actions, cands, trace, use_lineage=False)
    out = extract._cap(expanded, trace, phase='recall_candidate_final')
    extract._audit(trace, 'consolidate', 'completed', items=out, quality_profile='recall_candidate_v1')
    return out


def cache_key(llm, cands):
    listing = '\n'.join(f"[{i}] ({c['type']}) {c['text']}"
                        + (f" | owner cue: {c['owner_cue']}" if c.get('owner_cue') else '')
                        + (f" | deadline: {c['deadline']}" if c.get('deadline') else '')
                        for i, c in enumerate(cands))
    key = hashlib.sha256(('recall-candidate-capped-samples-v1' + baseline_prompts.CONSOLIDATE_SYSTEM
                         + llm.model + listing + (config.EFFORT_CONSOLIDATE or '')
                         + json.dumps(config.MAX_ITEMS, sort_keys=True)).encode()).hexdigest()
    return key, listing


def consolidate(llm, cands, cache=None, trace=None):
    if len(cands) <= 1:
        extract._audit(trace, 'consolidate', 'skipped', reason='at_most_one_candidate', items=cands)
        return cands
    key, listing = cache_key(llm, cands)
    saved = json.loads(cache.read_text(encoding='utf-8')) if cache and cache.exists() else {}
    runs = saved.get('runs', []) if saved.get('key') == key else []
    details = saved.get('run_traces', []) if saved.get('key') == key else []
    details += [None] * max(0, len(runs) - len(details))
    for n, items in enumerate(runs[:config.CONSOLIDATE_SAMPLES]):
        extract._audit(trace, 'consolidate', 'sample', sample=n, source='cache', cache_key=key,
                       items=items, recorded_attempts=details[n], detail_available=details[n] is not None,
                       cache_origin=saved.get('origin'))
    missing = max(0, config.CONSOLIDATE_SAMPLES - len(runs))
    def one_run(n):
        attempts = []
        user = 'Candidates:\n' + listing
        for attempt in range(3):
            raw = llm.forced_tool(baseline_prompts.CONSOLIDATE_SYSTEM, user, 'submit_final_items',
                                  extract.CONSOLIDATE_SCHEMA, effort=config.EFFORT_CONSOLIDATE, stage='consolidate')
            detail = StageTrace()
            groups = raw.get('items') if isinstance(raw, dict) else None
            detail.log(stage='consolidate', event='raw_groups', groups=groups, raw_response=raw)
            # Keep 414 per-sample caps, while retaining the current members-type guard.
            items = extract._merge_groups(groups, cands, detail, apply_cap=True)
            attempts.append({'attempt': attempt, 'events': detail.events})
            extract._audit(trace, 'consolidate', 'attempt', sample=n, attempt=attempt, source='model',
                           cache_key=key, events=detail.events, items=items)
            if items:
                return items, attempts
            user = ('Candidates:\n' + listing + '\nCorrection: return a nonempty usable items array. '
                    'Each group requires supported text, a valid type, and members as candidate INTEGER indexes '
                    f'from 0 to {len(cands)-1}. Do not use objects, participant IDs, strings or booleans as members.')
        return None, attempts
    if missing:
        with stage_pool(max(1, min(missing, config.WORKERS)), getattr(llm, 'control', None)) as pool:
            for items, attempts in pool.map(one_run, range(len(runs), len(runs) + missing)):
                if items:
                    runs.append(items)
                    details.append(attempts)
        if cache and runs:
            write_json(cache, {'key': key, 'runs': runs, 'run_traces': details,
                              'quality_profile': 'recall_candidate_v1'}, getattr(llm, 'control', None))
    runs = runs[:config.CONSOLIDATE_SAMPLES]
    extract._audit(trace, 'consolidate', 'samples', runs=runs, cache_key=key)
    return finish_samples(runs, cands, trace)
