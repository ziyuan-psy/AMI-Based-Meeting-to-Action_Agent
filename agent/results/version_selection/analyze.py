"""Describe direct content effects separately from context-sensitive judge alignment."""
from __future__ import annotations
import json,copy,collections
from study import ROOT,OLD,ISSUE,REPO,MEETINGS,load,save,sha
from score import ARMS
def key(i):return i['type'],i['text']
def run():
    rows=[];losses=[];gains=[];p2cases=[]
    for mid in MEETINGS:
        locations=load(ROOT/'scoring_locations.json') if (ROOT/'scoring_locations.json').exists() else {}
        scorepath=ROOT/locations.get(mid,'scoring/'+mid)/'result.json'
        if not scorepath.exists():continue
        scores=load(scorepath)
        if 'baseline' not in scores.get('arms',{}):continue
        inputs=load(scorepath.parent/'inputs.json');base=inputs['records']['baseline'];baseline={key(i):i for i in base if i['type']!='abstract'}
        base_ids={i['id']:i for i in base};base_pairs={key(base_ids[s]):g for s,g in scores['arms']['baseline']['matches'].items()}
        for arm in ARMS[1:]:
            if arm not in scores['arms']:continue
            current={key(i):i for i in inputs['records'][arm] if i['type']!='abstract'}
            current_ids={i['id']:i for i in inputs['records'][arm]}
            pairs={key(current_ids[s]):g for s,g in scores['arms'][arm]['matches'].items()}
            lost=[{'type':k[0],'text':k[1],'baseline_gold_id':g,'new_gold_id':pairs.get(k),
                   'cause':'unchanged_text_alignment_changed' if k in current else 'text_removed_or_rewritten'}
                  for k,g in base_pairs.items() if pairs.get(k)!=g]
            gained=[{'type':k[0],'text':k[1],'gold_id':g,'baseline_gold_id':base_pairs.get(k),
                     'cause':'unchanged_text_alignment_changed' if k in baseline else 'text_added_or_rewritten'}
                    for k,g in pairs.items() if base_pairs.get(k)!=g]
            row={'meeting_id':mid,'arm':arm,'unchanged_texts':len(baseline.keys()&current.keys()),
                 'removed_texts':[{'type':k[0],'text':k[1]} for k in baseline.keys()-current.keys()],
                 'added_texts':[{'type':k[0],'text':k[1]} for k in current.keys()-baseline.keys()],
                 'lost_matches':lost,'gained_matches':gained}
            rows.append(row);losses.extend(dict(meeting_id=mid,arm=arm,**i) for i in lost);gains.extend(dict(meeting_id=mid,arm=arm,**i) for i in gained)
        pack=load(ROOT/'fixed_inputs'/(mid+'.json'));acts=pack['acts'];positions={a['id']:n for n,a in enumerate(acts)}
        outcome_path=ROOT/'boundaries'/mid/'p2_current/p2.json'
        if not outcome_path.exists():continue
        outcomes=load(outcome_path)['items']
        for n,o in enumerate(outcomes):
            if o['recordable'] and o['item_text']==pack['normalized'][n]['text']:continue
            item=pack['normalized'][n];ids=item['evidence_da_ids'];indexes=set()
            for da in ids:
                p=positions[da];indexes.update(range(max(0,p-2),min(len(acts),p+3)))
            p2cases.append({'meeting_id':mid,'item_key':o['item_key'],'type':item['type'],
                'input_text':item['text'],'output_text':o['item_text'],'recordable':o['recordable'],
                'baseline_gold_id':base_pairs.get(key(item)), 'termination_reason':o['termination_reason'],
                'checks':o['checks'],'unresolved_fields':o['unresolved_fields'],
                'input_evidence_with_neighbors':[acts[p] for p in sorted(indexes)],
                'observed_evidence_da_ids':o['observed_evidence_da_ids'],
                'history':o['history']})
    aggregates={}
    for arm in ARMS[1:]:
        r=[i for i in rows if i['arm']==arm]
        aggregates[arm]={'removed_or_rewritten_texts':sum(len(i['removed_texts']) for i in r),
                         'added_or_rewritten_texts':sum(len(i['added_texts']) for i in r),
                         'lost_baseline_pairs_same_text':sum(i['cause']=='unchanged_text_alignment_changed' for i in losses if i['arm']==arm),
                         'lost_baseline_pairs_text_removed_or_rewritten':sum(i['cause']=='text_removed_or_rewritten' for i in losses if i['arm']==arm),
                         'gained_pairs_same_text':sum(i['cause']=='unchanged_text_alignment_changed' for i in gains if i['arm']==arm),
                         'gained_pairs_added_or_rewritten':sum(i['cause']=='text_added_or_rewritten' for i in gains if i['arm']==arm)}
    save(ROOT/'content_and_alignment.json',{'scored_meetings':len({i['meeting_id'] for i in rows}),
        'note':'Alignment flips may reflect changed competing claims/one-to-one matching as well as judge randomness. They are NOT direct deletion by the module; original protocol remains unchanged.',
        'aggregate':aggregates,'per_meeting':rows,'lost_matches':losses,'gained_matches':gains})
    save(ROOT/'p2_case_audit_inputs.json',p2cases)
    print(json.dumps({'scored_meetings':len({i['meeting_id'] for i in rows}),'aggregate':aggregates},ensure_ascii=False))
if __name__=='__main__':run()
