"""Flat P2 wire format. Evidence remains an explicit model selection, bound by the controller."""
from __future__ import annotations

import copy

from .agent import tool_defs
from .documents import SELECTION
from .extract import VALID_STATUS
from .linking import LINK_SCHEMA

CHECK_KEYS = {'statement':'statement', 'owner':'owner_check', 'deadline':'deadline_check',
              'status':'status_check', 'conflict':'conflict_check', 'relation':'relation_check'}
NEXT_ACTIONS = ['accept','retrieve','search_again','revise_statement','request_clarification','stop']


def compact_tool(allow_prior, item_type, require_relation=False):
    base = copy.deepcopy(tool_defs(allow_prior)[-1]['input_schema']['properties'])
    properties = {k:base[k] for k in ['status','related_items']}
    properties['status'] = {'enum':sorted(VALID_STATUS[item_type])}
    required = ['status','related_items','next_action']
    if item_type == 'actions':
        for name in ('owner','owner_basis','deadline','deadline_basis'):
            properties[name] = base[name]
            required.append(name)
    for check, key in CHECK_KEYS.items():
        if check in ('owner','deadline') and item_type != 'actions':
            continue
        results = ['pass','fail','uncertain']
        if check == 'relation' and not require_relation:
            results.append('not_applicable')
        finding = {'type':'object','properties':{
            'result':{'enum':results}, 'reason':{'type':'string','minLength':1},
            'evidence_da_ids':{'type':'array','items':{'type':'string'},
                'description':'Select full dialogue IDs actually delivered in observed_dialogue or a tool result. Empty only when uncertain/not applicable. Never use document IDs here.'},
            'documents':{'type':'array','items':copy.deepcopy(SELECTION)}},
            'required':['result','reason','evidence_da_ids'],'additionalProperties':False}
        # Keep the root a plain object for the OpenAI-compatible gateway. A positive check needs
        # an explicit selection; conflict alone may reuse the selected statement evidence scope.
        if check != 'conflict':
            finding['allOf'] = [{'if':{'properties':{'result':{'const':'pass'}}},
                'then':{'properties':{'evidence_da_ids':{'minItems':1}}}}]
        if check == 'conflict':
            finding['description']='Check actual contradictory statements in delivered evidence. Missing deadline/owner or limited global coverage is not a contradiction. Use pass within the reviewed evidence scope when none is found; do not mark the core statement uncertain merely because an auxiliary field is missing.'
            finding['properties']['affected_fields'] = {'type':'array','items':{'enum':['statement','owner','deadline','status','related_items']}}
            finding['required'].append('affected_fields')
        if check == 'relation':
            finding['properties']['links'] = {'type':'array','items':copy.deepcopy(LINK_SCHEMA)}
        properties[key] = finding
        required.append(key)
    properties['next_action'] = {'enum':NEXT_ACTIONS,
        'description':'Choose clarification/stop when no useful evidence remains, including missing owner/deadline; accept requires all checks, including required history. Retrieve/search before accepting an unchecked historical relation. Revise only when the advertised revision tool offers an exact removable suffix; empty arguments are invalid.'}
    return {'name':'assess_item','description':'Assess this item using flat fields and explicit dialogue ID selections. No fields/checks wrapper. Code validates every selection and determines the actual next action.',
            'input_schema':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}


def canonical_assessment(payload, item_type):
    checks = {}
    for check,key in CHECK_KEYS.items():
        if key not in payload:
            continue
        finding = copy.deepcopy(payload[key])
        finding['quotes'] = finding.pop('evidence_da_ids')
        finding['fields'] = finding.pop('affected_fields', []) if check == 'conflict' else ['related_items' if check == 'relation' else check]
        checks[check] = finding
    values = {k:copy.deepcopy(payload[k]) for k in ('status','related_items','owner','owner_basis','deadline','deadline_basis') if k in payload}
    values.update(prior_context_needed=False, used_participant_lookup=False,
                  evidence_da_ids=list(payload['statement']['evidence_da_ids']))
    return {'fields':values,'checks':checks,'next_action':payload['next_action']}
