"""Freeze real development acceptance artifacts after automated and targeted source review."""
import json
from collections import Counter
from pathlib import Path
import audit
from mta.store import Store
from mta.observation import sha256,write_text
c=audit.c

def main():
    audit.main();a=c.load(c.ROOT/'audit.json');s=c.load(c.ROOT/'study.json')
    assert a['status']=='AUTOMATED_CHECKS_PASSED_SOURCE_REVIEW_REQUIRED' and a['completed']==4
    c.route();first=a['meetings'][0]
    # These two exact real outputs and all quoted dialogue were inspected by the developer.
    specifications=[('TS3006a#6','MTD023UID','TS3006a.A.dialog-act.vkaraisk.149','TS3006a.B.dialog-act.vkaraisk.59'),
                    ('TS3006a#7','MTD024ME','TS3006a.A.dialog-act.vkaraisk.151','TS3006a.D.dialog-act.vkaraisk.100')]
    cases=[]
    for key,owner,assignment,ack in specifications:
        case=next(x for x in first['restored_source_review'] if x['item_key']==key)
        record=case['record'];ids={r['locator'] for r in record['reference'] if r['source_type']=='dialogue'}
        assert case['successful_finish'] and {assignment,ack}<=ids and record['owner']==[owner]
        assert record['owner_basis']=='explicit' and record['deadline'] is None and record['deadline_basis']=='missing'
        assert next(x for x in first['citation_checks'] if x['item_key']==key)['all_cited_dialogue_delivered']
        cases.append({**case,'verdict':'SUPPORTED_TASK_AND_OWNER',
            'review_method':'Developer read actual final references and frozen transcript lines; no gold or model judge.',
            'reason':'PM explicitly assigns the named role; the participant with that role acknowledges it. No supported deadline is asserted.'})
    review={'status':'TARGETED_SOURCE_REVIEW_PASSED','reviewer':'developer source inspection',
            'new_user_manual_confirmation':False,'scope':'Two specified recovered TS3006a Action tasks, owners and final citations; not every output claim.',
            'cases':cases}
    c.save(c.ROOT/'source_review.json',review)
    histories=[];empty_attempts=[]
    for mid in c.MEETINGS:
        folder=c.ROOT/'run/s2';store=Store(mid,True,folder)
        record=c.load(folder/(mid+'.json'));prior={r['id']:r for r in store.prior_items()}
        trace=audit.jsonl(folder/(mid+'.trace.jsonl'))
        for event in audit.jsonl(folder/(mid+'.stages.jsonl')):
            if event.get('stage')=='consolidate' and event.get('event')=='attempt' and not event.get('items'):
                empty_attempts.append({'meeting_id':mid,'sample':event.get('sample'),'attempt':event.get('attempt')})
        for n,row in enumerate(r for r in record if r['type']!='abstract'):
            key=f'{mid}#{n}'
            for relation in row.get('related_items',[]):
                histories.append({'old_record':prior[relation['id']],
                    'retrieval_and_agent_choices':[e for e in trace if e.get('item')==key],
                    'new_record':row,'relation':relation['relation'],'scope_check':'same-series earlier recorded item',
                    'semantic_verdict':'NOT_CERTIFIED_BY_FULL_P2; evaluator must independently assess before/after support'})
    c.save(c.ROOT/'cross_meeting_cases.json',histories)
    formats=Counter()
    for path in sorted((c.ROOT/'run').rglob('*')):
        if path.is_file() and path.suffix=='.json':
            c.load(path);formats['json']+=1
        elif path.is_file() and path.suffix=='.jsonl':
            audit.jsonl(path);formats['jsonl']+=1
    c.save(c.ROOT/'format_audit.json',{'status':'PASSED','native_artifact_files_parsed':dict(formats)})
    calls=sum(m['runtime_control']['attempted_calls'] for m in a['meetings'])
    tokens=sum(m['runtime_control']['charged_tokens'] for m in a['meetings'])
    errors=[e for m in a['meetings'] for e in m['recoverable_errors']]
    final={'status':'PASSED_WITH_DOCUMENTED_LIMITATIONS','created_at':c.utc(),'runtime_reference_commit':s['commit'],
        'profile':s['runtime']['quality_policy']['name'],'completed':4,'scheduled':4,'development_population':16,
        'real_api_calls':calls,'charged_tokens':tokens,'total_meeting_seconds':round(sum(m['elapsed_seconds'] for m in a['meetings']),3),
        'gold_read':False,'heldout_opened':False,'source_changed':False,'p2_enabled':False,
        'targeted_source_review':'TARGETED_SOURCE_REVIEW_PASSED','cross_meeting_links':len(histories),
        'recoverable_agent_errors':errors,'empty_merge_response_attempts':empty_attempts,
        'f1_measured':False,'final_evaluation_owner':'teammate','heldout_evaluation_started':False,
        'commit_created':False,'pushed':False,
        'limitations':['Four-meeting runtime acceptance, not fresh 16-meeting quality measurement.',
                       'Full P2 is intentionally disabled; targeted source checks do not certify all claims, conflicts, documents or links.',
                       'Empty P2 clarification sidecars do not prove absence of uncertainty.',
                       'Recoverable model/tool mistakes remain in original traces; no favorable regeneration was selected.']}
    c.save(c.ROOT/'acceptance.json',final)
    lines=['# Real development acceptance: 66a21d7 / recall_candidate_v1','',
        '**Verdict: PASSED WITH DOCUMENTED LIMITATIONS.** All four cold, real API S2 meetings completed once in a→d order. '
        'The runtime is ready to hand to the teammate for the fixed final evaluation; this is not a claim of final accuracy.','',
        '| Meeting | Completion | Seconds | Model attempts | Charged tokens | A / D / I | Links |',
        '|---|---|---:|---:|---:|---|---:|']
    for m in a['meetings']:
        counts=m['item_counts'];control=m['runtime_control']
        lines.append(f"| {m['meeting_id']} | {m['status']} | {m['elapsed_seconds']:.1f} | {control['attempted_calls']} | {control['charged_tokens']:,} | {counts.get('actions',0)} / {counts.get('decisions',0)} / {counts.get('problems',0)} | {len(m['cross_meeting_links'])} |")
    lines+=['',f'Total: {calls} model attempts, {tokens:,} charged tokens, {final["total_meeting_seconds"]:.1f} summed meeting seconds. '
        'Counts include pipeline format retries and follow-up turns; judge calls: 0. Per-meeting budgets were unchanged and respected.','',
        '## What passed','',
        '- Actual TXT→parser→model extraction→merge/vote/recovery/cap→follow-up→summary→validated JSON/report publication.',
        '- Code/schema/input hashes, model, active prompts, config and effective policy stayed fixed. Fresh caches; no response replay or seeded prior outputs.',
        '- All JSON and JSONL parse, output schemas, report/sidecar identities and hashes, completion markers, checkpoints, request accounting, request/meeting timeouts and lookup/concurrency limits.',
        '- Final dialogue references exist and stay in the current/same-series earlier scope. Successful Agent finishes cite only dialogue actually delivered to that item.',
        f'- Independently parsed every native run JSON/JSONL: {formats["json"]} JSON files and {formats["jsonl"]} JSONL files; see `format_audit.json`.',
        '- The two targeted TS3006a UI/Marketing tasks contain the real PM assignment and recipient acknowledgement, correct explicit owner, and no invented deadline. See `source_review.json`.','',
        '## Recovery events and limits','',
        f'- {len(empty_attempts)} unusable consolidation response attempt(s) were retried by the pre-existing bounded protocol. {len(errors)} recoverable Agent error event(s) remain visible in `audit.json` and the original traces.',
        '- TS3006b passed a system-record ID to `read_dialogue`; this was rejected. Attempts to cite unread historical dialogue were also rejected before a finish using observed current support.',
        f'- {len(histories)} cross-meeting link(s) were generated. `cross_meeting_cases.json` preserves old record → actual retrieval/tool returns → Agent finish → new record. Source/scope validity is checked; semantic correctness of every relation is not certified.',
        '- Full P2 is intentionally disabled for this recall candidate. Reports remain Draft candidate. Empty P2/clarification sidecars are an explicit disabled state, not a statement that all evidence/conflicts/fields passed.',
        '- Higher Issue recall admits unmatched/low-relevance hypotheses. Task/Decision duplication, granularity, qualifiers and historical relations remain evaluation concerns; no tuning or filtering was performed during acceptance.',
        '- No new F1 was computed here. The .464 development pooled F1 belongs to the earlier 16-meeting fixed-response replay, not this online run or the held-out set.','',
        '## Handoff and preservation','',
        '- Final evaluation stays with the teammate. Neither held-out series was opened or run. See `../HANDOFF.md` for the fixed entry point, cache policy, metrics and evaluation-adapter work.',
        f'- {a["protected_files_checked"]} UI/annotation files and {a["preserved_prior_artifacts"]} prior frozen artifacts were hash-checked unchanged. Existing untracked experiment directories were not overwritten.',
        '- No production source/config, UI or annotation document was changed. Only this acceptance package was added. No commit or push.',
        '- The transfer manifest covers this entire package; `python ../verify_package.py` performs an offline integrity check.','']
    write_text(c.ROOT/'REPORT.md','\n'.join(lines))
    # Manifest covers outer entry points as well as the frozen inner data, excluding itself and bytecode.
    hashes={p.relative_to(c.HERE).as_posix():sha256(p) for p in sorted(c.HERE.rglob('*'))
            if p.is_file() and p!=c.HERE/'manifest.json' and '__pycache__' not in p.parts}
    c.save(c.HERE/'manifest.json',{'created_at':c.utc(),'runtime_reference_commit':s['commit'],
                                 'artifact_sha256':hashes})
    print(json.dumps({k:final[k] for k in ('status','completed','real_api_calls','charged_tokens','cross_meeting_links','heldout_opened')},ensure_ascii=False))

if __name__=='__main__':main()
