"""One deletion of an unsupported trailing qualifier, followed by mandatory reassessment."""
import re

TIME_LITERAL = re.compile(r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|\b\d{4}-\d{2}-\d{2}\b',re.I)


def unanchored_time_literals(text, quotes, current):
    """A named weekday/calendar date cannot be inferred from unrelated quoted time wording.

    This is a necessary provenance check, not a general semantic entailment judge.
    Relative wording and other conditions still require the model's evidence checks.
    """
    wanted={m.group().casefold() for m in TIME_LITERAL.finditer(text or '')}
    source=' '.join(q['quote'] for q in quotes if q['da_id'].startswith(current+'.'))
    anchored={m.group().casefold() for m in TIME_LITERAL.finditer(source)}
    return sorted(wanted-anchored)


def revision_candidates(text):
    """Syntax-only choices. The Agent must still establish which qualifier is unsupported."""
    choices=[]
    for match in re.finditer(r'\b(after|before|by|within|at|on|using|with|for)\b',text,re.I):
        suffix=text[match.start():].strip()
        try:_delete_suffix(text,suffix)
        except ValueError:continue
        choices.append(suffix)
        normalized=suffix.rstrip('.!?;')
        if normalized!=suffix:choices.append(normalized)
    return choices


def revision_tool(text=None, evidence_ids=None):
    return {"name": "revise_statement", "description":
        "Once only: remove an unsupported trailing time/method/detail qualifier from the current text. "
        "Keep actor, task, object, polarity and modality. This never accepts the item: all checks must run again.",
        "input_schema": {"type": "object", "properties": {
            "remove_suffix": {"type": "string", "minLength": 3,**({'enum':revision_candidates(text)} if text is not None else {})},
            "reason": {"type": "string", "minLength": 1},
            "evidence_da_ids": {"type": "array", "minItems": 1,"uniqueItems":True,
                "items": {"type": "string",**({'enum':list(evidence_ids)} if evidence_ids is not None else {})}}},
            "required": ["remove_suffix", "reason", "evidence_da_ids"], "additionalProperties": False}}


def deletion(text, suffix, selected, observed, current):
    """No new wording or automatic modality/actor/negation correction is permitted."""
    if not selected or any(d not in observed or not d.startswith(current + ".") for d in selected):
        raise ValueError("revision_requires_delivered_current_evidence")
    return _delete_suffix(text,suffix)


def _delete_suffix(text,suffix):
    core, suffix = text.rstrip().rstrip(".!?;"), suffix.strip().rstrip(".!?;")
    if not core.endswith(suffix) or len(core) == len(suffix):
        raise ValueError("revision_must_remove_an_exact_trailing_suffix")
    if not core[-len(suffix)-1].isspace():
        raise ValueError("revision_must_start_at_a_word_boundary")
    if not re.match(r"^(after|before|by|within|at|on|using|with|for)\b", suffix, re.I):
        raise ValueError("revision_is_limited_to_trailing_qualifiers")
    if re.search(r"\b(no|not|never|only|unless|except|without|instead|if|until|must|may|might|could|should)\b", suffix, re.I):
        raise ValueError("revision_cannot_remove_polarity_modality_or_conditions")
    if re.search(r"\b(approv\w*|consent|permission|contingent|dependent)\b|subject to|provided that", suffix, re.I):
        raise ValueError("revision_cannot_remove_an_approval_condition")
    revised = core[:-len(suffix)].rstrip(" ,;:")
    if re.search(r'\b(only|not|never|unless|except|without|instead|if|until|dependent|contingent)\s*$',revised,re.I):
        raise ValueError('revision_cannot_detach_a_condition_or_polarity')
    words, original_words = re.findall(r"\w+", revised), re.findall(r"\w+", text)
    if len(words) < 4 or len(words) < len(original_words) / 2:
        raise ValueError("revision_would_remove_the_core_claim")
    return revised + ("." if text.rstrip().endswith(".") else "")
