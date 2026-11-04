"""Bounded review cues and lookup menus; none of these helpers assert a fact."""
from __future__ import annotations

import copy
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


def deadline_review_evidence(checks, current):
    """Revisit only time cues in explicitly selected, validated current statement evidence.

    The whole original line is delivered for semantic review. A cue is never a deadline assignment.
    """
    return [copy.deepcopy(q) for q in checks['statement']['quotes']
            if q['da_id'].startswith(current + '.') and _TIME_CUE.search(q['quote'])]


def has_content_query(query):
    """Meeting/record locators and item-type labels alone cannot search item text."""
    return bool(set(tokenize(_LOCATOR.sub(' ', query))) - _NONCONTENT)


def fresh_lookup_tools(tools, executed):
    """Advertise successful/empty lookup arguments as unavailable on this immutable item store."""
    result = copy.deepcopy(tools)
    for tool in result:
        prior = [args for name, args in executed if name == tool['name']]
        if prior:
            tool['input_schema'].setdefault('allOf', []).extend(
                {'not': {'required': list(args),
                         'properties': {k: {'const': v} for k, v in args.items()}}}
                for args in prior)
            tool['description'] += ' Do not repeat an executed lookup; change query/window/page or read a returned evidence locator.'
    return result
