"""Reuse fixed-TXT extraction boundaries, without conflating evidence overlap with semantic TP."""
from __future__ import annotations
import collections,copy,json
from pathlib import Path
from study import REPO,ROOT,OLD,ISSUE,MEETINGS,load,save,sha,module
from mta.regression import is_placeholder
from mta.evaluate import TYPES

def main():
    legacy=module('baseline414','extract');rows=[]
    exclusions=set(load(ROOT/'scoring_protocol.json')['fewshot_excluded_ids'])
    for mid in MEETINGS:
        current=OLD/'run/s2'/(mid+'.run.json')
        if not current.exists():current=REPO/load(OLD/'reuse.json')['source_cohort']/'run/s2'/(mid+'.run.json')
        issue=ISSUE/'run/s2'/(mid+'.run.json');meta213=load(current);meta_issue=load(issue)
        input_sha=sha(ROOT/'inputs/transcripts'/(mid+'.txt'))
        assert meta213['input']['sha256']==meta_issue['input']['sha256']==input_sha
        assert meta213['model']==meta_issue['model']==load(ROOT/'study.json')['runtime']['model']
        assert meta213['config']==meta_issue['config']==load(ROOT/'study.json')['runtime']['config']
        cache=OLD/'baseline/_cache'/('extract_False_'+mid+'.json');saved=load(cache)
        pools={'baseline414':saved['chunk_pass']['candidates']+legacy.union_actions(saved['action_pass']['samples'][:2]),
               'committed213':load(Path(meta213['checkpoints']['extracted'])),
               'issue_current':load(Path(meta_issue['checkpoints']['extracted']))}
        gold=load(ROOT/'scoring_inputs/gold'/(mid+'.json'));counts={};coverage={}
        for arm,pool in pools.items():
            counts[arm]={t:sum(i['type']==t for i in pool) for t in TYPES};coverage[arm]={}
            for typ in TYPES:
                gg=[g for g in gold if g['type']==typ]
                if any(is_placeholder(g) for g in gg):coverage[arm][typ]={'unscored':'NA category'};continue
                gg=[g for g in gg if g['id'] not in exclusions];hits=[]
                for g in gg:
                    refs={r['locator'].split(' ')[0] for r in g['reference'] if r['source_type']=='dialogue'}
                    matches=[n for n,i in enumerate(pool) if i['type']==typ and refs.intersection(i['evidence_da_ids'])]
                    if matches:hits.append({'gold_id':g['id'],'candidate_indexes':matches})
                coverage[arm][typ]={'shared_evidence_gold':len(hits),'gold':len(gg),'hits':hits}
        rows.append({'meeting_id':mid,'input_sha256':input_sha,'candidate_counts':counts,
             'shared_evidence_proxy':coverage,'checkpoint_sha256':{'baseline_cache':sha(cache),
               'committed213':sha(Path(meta213['checkpoints']['extracted'])),'issue_current':sha(Path(meta_issue['checkpoints']['extracted']))}})
    totals={a:{t:sum(r['candidate_counts'][a][t] for r in rows) for t in TYPES} for a in pools}
    coverage={a:{t:{k:sum(r['shared_evidence_proxy'][a][t].get(k,0) for r in rows) for k in ('shared_evidence_gold','gold')} for t in TYPES} for a in pools}
    save(ROOT/'extraction_audit.json',{'meetings':MEETINGS,'api_calls':0,'candidate_counts':totals,
         'shared_evidence_proxy':coverage,'per_meeting':rows,
         'interpretation':'Same frozen TXT/model/config. Existing genuine candidate responses reused. Shared evidence is only a localization proxy: NOT semantic recall, one-to-one matching, correctness or an F1 score. Old versus new prompt responses also contain sampling variation. Only same-response restoration experiments isolate deterministic code changes.'})
    print(json.dumps({'candidate_counts':totals,'shared_evidence_proxy':coverage,'api_calls':0},ensure_ascii=False))
if __name__=='__main__':main()
