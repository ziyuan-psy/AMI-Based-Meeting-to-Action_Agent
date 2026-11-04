"""Explicit content profiles, independent of generation and resource settings."""
from __future__ import annotations

import hashlib
from . import config, prompts, baseline_prompts

DEVELOPMENT = 'development'
RECALL_CANDIDATE = 'recall_candidate_v1'
PROFILES = (DEVELOPMENT, RECALL_CANDIDATE)


def effective(mode=None):
    name = config.QUALITY_PROFILE
    if name not in PROFILES:
        raise ValueError('Unknown quality profile: ' + str(name))
    candidate = name == RECALL_CANDIDATE and mode != 'b0'
    active = baseline_prompts if candidate else prompts
    selected = {'extract': active.EXTRACT_SYSTEM, 'consolidate': active.CONSOLIDATE_SYSTEM,
                'follow_up': prompts.AGENT_SYSTEM}
    if not candidate:
        selected.update(decision_consolidate=prompts.DECISION_CONSOLIDATE_SYSTEM, p2=prompts.P2_SYSTEM)
    return {'name': name, 'version': 1, 'candidate': candidate, 'mode': mode,
            'content_anchor': '414a31f' if candidate else 'development',
            'action_recovery': 'type_aware',
            'issue_recovery': 'original_hypotheses_exact_duplicate_check' if candidate else 'development',
            'cap_order': '414_stable' if candidate else 'development',
            'decision_second_pass': not candidate and mode != 'b0' and config.DECISION_RECONSOLIDATE,
            'selection_enabled': not candidate and mode != 'b0' and config.ITEM_SELECTION_ENABLED,
            'p2_enabled': not candidate and mode != 'b0' and config.P2_ENABLED,
            'follow_up': 'flagged_delivered_sources_only' if candidate else 'development',
            'assurance': 'targeted_follow_up_not_full_p2' if candidate else 'configured_checks',
            'prompt_sha256': {key: hashlib.sha256(value.encode('utf-8')).hexdigest() for key, value in selected.items()}}
