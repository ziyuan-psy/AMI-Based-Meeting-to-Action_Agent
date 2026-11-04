"""Bounded review cues and lookup menus; none of these helpers assert a fact."""
from __future__ import annotations

import copy
import json
import re

from .store import tokenize


_TIME_CUE = re.compile(
    r"\b(?:by|before|after|within|until|at|in)\b.{0,65}"
    r"\b(?:meeting|session|presentation|lunch|today|tomorrow|tonight|"
    r"days?|weeks?|months?|hours?|minutes?|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:next|this|end of|conclusion of)\s+(?:our\s+|the\s+)?(?:meeting|session|week|month)\b",
    re.I,
)
_LOCATOR = re.compile(r"\b[A-Za-z]+\d+[a-d](?:[.#][\w.-]+)?\b", re.I)
_NONCONTENT = set('action actions decision decisions problem problems issue issues item items '
                  'record records meeting meetings prior earlier previous current the a an of in for '
                  'from to and or all this that'.split())


def deadline_review_evidence(checks, current, observed=None, candidate_ids=()):
    """Review time cues in the validated statement or delivered original candidate evidence.

    A response-only statement selection may omit the timing in the original request. Include
    that candidate's delivered proof, not arbitrary surrounding dialogue. A cue never assigns time.
    """
    selected={q['da_id']:copy.deepcopy(q) for q in checks['statement']['quotes']}
    for identifier in candidate_ids:
        if identifier in (observed or {}) and identifier.startswith(current+'.'):
            selected.setdefault(identifier,{'da_id':identifier,'quote':observed[identifier]['text']})
    return [q for q in selected.values() if q['da_id'].startswith(current+'.') and _TIME_CUE.search(q['quote'])]


def has_content_query(query):
    """Meeting/record locators and item-type labels alone cannot search item text."""
    return bool(set(tokenize(_LOCATOR.sub(' ', query))) - _NONCONTENT)


def fresh_lookup_tools(tools, executed):
    """Expose executed argument sets; the controller rejects exact/token-normalized repeats.

    Keep function schemas plain root objects for the OpenAI-compatible gateway. Root logical
    constraints fail before a model response; a property-only ban would incorrectly block a
    useful scope, type or page change with the same query.
    """
    result = copy.deepcopy(tools)
    for tool in result:
        prior = [args for name, args in executed if name == tool['name']]
        if prior:
            tool['description'] += (' Do not repeat these already executed argument sets: '
                + json.dumps(prior, ensure_ascii=False, sort_keys=True)
                + '. Change query/scope/type/window/page or read a returned evidence locator. Code rejects repeats before spending lookup budget.')
    return result
