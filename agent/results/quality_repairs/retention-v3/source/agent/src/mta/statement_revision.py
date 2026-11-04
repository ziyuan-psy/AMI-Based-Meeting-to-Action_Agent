"""One deletion of an unsupported trailing qualifier, followed by mandatory reassessment."""
import re


def revision_tool():
    return {"name": "revise_statement", "description":
        "Once only: remove an unsupported trailing time/method/detail qualifier from the current text. "
        "Keep actor, task, object, polarity and modality. This never accepts the item: all checks must run again.",
        "input_schema": {"type": "object", "properties": {
            "remove_suffix": {"type": "string", "minLength": 3},
            "reason": {"type": "string", "minLength": 1},
            "evidence_da_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            "required": ["remove_suffix", "reason", "evidence_da_ids"], "additionalProperties": False}}


def deletion(text, suffix, selected, observed, current):
    """No new wording or automatic modality/actor/negation correction is permitted."""
    if not selected or any(d not in observed or not d.startswith(current + ".") for d in selected):
        raise ValueError("revision_requires_delivered_current_evidence")
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
    words, original_words = re.findall(r"\w+", revised), re.findall(r"\w+", text)
    if len(words) < 4 or len(words) < len(original_words) / 2:
        raise ValueError("revision_would_remove_the_core_claim")
    return revised + ("." if text.rstrip().endswith(".") else "")
