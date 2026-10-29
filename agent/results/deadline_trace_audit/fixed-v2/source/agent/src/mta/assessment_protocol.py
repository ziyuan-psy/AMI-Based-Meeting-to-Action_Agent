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
    required = ['status','related_items']
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
        if check!='relation' or allow_prior:required.append(key)
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
    return {'fields':values,'checks':checks,**({'next_action':payload['next_action']} if 'next_action' in payload else {})}


def complete_inactive_metadata(payload,no_prior_scope=False):
    """Repair only scope-known inactive checks and omitted evidence on non-affirmative findings.

    Never supply a positive result, a supporting ID, or a missing positive explanation.
    """
    payload=copy.deepcopy(payload);changes=[]
    if no_prior_scope and not payload.get('related_items'):
        relation=payload.get('relation_check')
        if relation is None:
            payload['relation_check']={'result':'not_applicable','reason':'no_readable_prior_meetings','evidence_da_ids':[]}
            changes.append('inactive_relation_from_run_scope')
        elif isinstance(relation,dict) and relation.get('result')=='not_applicable':
            if relation.get('reason') is None or isinstance(relation.get('reason'),str) and not relation['reason'].strip():
                relation['reason']='no_readable_prior_meetings';changes.append('inactive_relation_reason_from_run_scope')
            if relation.get('evidence_da_ids') is None:
                relation['evidence_da_ids']=[];changes.append('empty_evidence_for_inactive_relation')
    for key in CHECK_KEYS.values():
        finding=payload.get(key)
        if isinstance(finding,dict) and finding.get('result') in ('uncertain','fail'):
            if finding.get('evidence_da_ids') is None:
                finding['evidence_da_ids']=[];changes.append('empty_evidence_for_nonaffirmative:'+key)
            if finding.get('reason') is None or isinstance(finding.get('reason'),str) and not finding['reason'].strip():
                finding['reason']='model_reported_'+finding['result']+'_without_explanation'
                changes.append('missing_nonaffirmative_explanation:'+key)
            if key=='conflict_check' and 'affected_fields' not in finding:
                finding['affected_fields']=['statement'];changes.append('unscoped_conflict_blocks_statement')
        if key=='conflict_check' and isinstance(finding,dict) and finding.get('result')=='pass' and 'evidence_da_ids' not in finding:
            finding['evidence_da_ids']=[];changes.append('no_conflict_uses_selected_statement_scope')
    return payload,changes
