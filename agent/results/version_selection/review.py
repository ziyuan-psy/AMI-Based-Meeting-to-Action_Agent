"""Developer source checks of decisive cases; not independent human final evaluation."""
from study import ROOT,load,save,sha

def run():
    cases=[]
    definitions=[
      ('ES2011b#4','overblocking',
       '证据明确要求 everyone 完成问卷，并为下次会议带来新想法。P2 将 owner/deadline/验收标准是否齐全作为 statement 的前提，隔离整条任务。这是核心任务与辅助字段混用的反例；不要求补造参与者 ID 或具体日期。',
       ['ES2011b.A.dialog-act.dharshi.172','ES2011b.A.dialog-act.dharshi.174']),
      ('ES2011b#11','overblocking',
       'P2 的 statement/status 已引用明确结论“we definitely want a power button and numbers”并判 pass；conflict 却把前面的竞品/备选机制讨论当成未解决矛盾，整条隔离。最终结论已交付给模型，因此不能解释为未读到结论。',
       ['ES2011b.A.dialog-act.dharshi.114']),
      ('ES2011a#5','do_not_equate_old_match_with_source_correctness',
       '旧评分将 All team members ... user requirements 匹配到 Marketing Expert 的任务。原句 161–163 先谈 Marketing，再说 we；不足以证明全部成员共同承担。不能为了复现旧 TP 自动认定这条宽化任务正确。P2 的理由也夹杂辅助字段要求，应单独区分范围问题和 owner/deadline 问题。',
       ['ES2011a.A.dialog-act.dharshi.161','ES2011a.A.dialog-act.dharshi.162','ES2011a.A.dialog-act.dharshi.163']),
    ]
    for key,verdict,reason,ids in definitions:
        mid=key.split('#')[0];n=int(key.split('#')[1]);pack=load(ROOT/'fixed_inputs'/(mid+'.json'))
        p=ROOT/'boundaries'/mid/'p2_current/p2.json';outcome=next(i for i in load(p)['items'] if i['item_key']==key)
        acts={a['id']:a for a in pack['acts']};assert not outcome['recordable']
        if verdict=='overblocking':assert set(ids)<=set(outcome['observed_evidence_da_ids'])
        cases.append({'item_key':key,'type':outcome['item_type'],'input_text':pack['normalized'][n]['text'],
          'verdict':verdict,'reason':reason,'source_lines':[acts[d] for d in ids],
          'check_results':outcome['checks'],'p2_artifact_sha256':sha(p),
          'fixed_input_sha256':sha(ROOT/'fixed_inputs'/(mid+'.json')),
          'reviewer':'coding_agent_developer_source_review','user_human_confirmation':False})
    # The very same old PM task remains in P2 output, yet the old matcher TP is gone.
    source=load(ROOT/'scoring/ES2011a/inputs.json');scores=load(ROOT/'scoring/ES2011a/result.json')
    old=next(i for i in source['records']['baseline'] if i['type']=='actions' and 'Project Manager will look' in i['text'])
    new=next(i for i in source['records']['p2_current'] if i['type']=='actions' and i['text']==old['text'])
    assert old['id'] in scores['arms']['baseline']['matches'] and new['id'] not in scores['arms']['p2_current']['matches']
    pack=load(ROOT/'fixed_inputs/ES2011a.json');acts={a['id']:a for a in pack['acts']}
    cases.append({'item_key':'ES2011a#2','verdict':'unchanged_text_alignment_flip_and_baseline_misattribution',
      'text':old['text'],'baseline_gold_id':scores['arms']['baseline']['matches'][old['id']],
      'p2_same_text_still_published':True,
      'reason':'原文本把任务交给 PM，但原句 145 先点名 Industrial Designer，148 才续说 working design。旧 matcher 仍给 TP；同样文本在 P2 输出仍存在，新组 matcher 不给 TP。这既不是 P2 删除任务，也不能据旧 TP 宣称基线归属正确。',
      'source_lines':[acts[d] for d in ('ES2011a.A.dialog-act.dharshi.145','ES2011a.A.dialog-act.dharshi.148')],
      'reviewer':'coding_agent_developer_source_review','user_human_confirmation':False})
    if (ROOT/'stable_retention/comparison.json').exists():
        mid='TS3006a';p=load(ROOT/'fixed_inputs'/(mid+'.json'));acts={a['id']:a for a in p['acts']}
        extra=load(ROOT/'stable_retention'/mid/'inputs.json')
        for label,needle,affirm in [('UI','User Interface Designer should specify the technical functions','TS3006a.B.dialog-act.vkaraisk.59'),
                                     ('ME','Expert should come up with user requirements','TS3006a.D.dialog-act.vkaraisk.100')]:
            line=next(a for a in p['acts'] if needle in a['text'])
            item=next(i for i in extra['record'] if i['text'] in {pair[1] for pair in extra['added']} and ('Interface' in i['text'] if label=='UI' else 'Marketing' in i['text']))
            references={r['locator'] for r in item['reference'] if r['source_type']=='dialogue'}
            cases.append({'meeting_id':mid,'type':'actions','text':item['text'],'verdict':'supported_type_aware_recovery',
                'source_lines':[line,acts[affirm]],'initial_reference_has_assignment':line['id'] in references,
                'reason':'类型恢复补回真实任务；保留原有排序后没有挤掉其他旧条目。ME 原候选引用只有回应及后续提问，未包含任务句151；组合版本应用时仍须补查并保留任务句引用，不把本次内容得分当作完整引用验证。',
                'reviewer':'coding_agent_developer_source_review','user_human_confirmation':False})
    pack=load(ROOT/'fixed_inputs/IS1008d.json');acts={a['id']:a for a in pack['acts']}
    replay_path=ROOT/'boundaries/IS1008d/replay.json';r=load(replay_path)
    item=next(i for i in r['records']['retention_actions'] if 'napkin' in i['text'])
    assert item['text'] not in {i['text'] for i in r['records']['baseline']}
    cases.append({'case_id':'IS1008d_ranking_promotes_in_meeting_procedure','type':'actions','text':item['text'],
        'verdict':'new_ranking_promotes_in_meeting_procedure',
        'reason':'同响应的新排序把当前会场内“现在传看原型、用纸巾拿”的操作纳入5条Action，替换旧池条目。原文是现场提议及回应，属于基准和现代规则均排除的会中操作；不能只因owner明确或覆盖新角色就提升到项目后续任务。',
        'source_lines':[acts[d] for d in ('IS1008d.C.dialog-act.s9553330.59','IS1008d.A.dialog-act.s9553330.34','IS1008d.B.dialog-act.s9553330.17')],
        'replay_sha256':sha(replay_path),'reviewer':'coding_agent_developer_source_review','user_human_confirmation':False})
    save(ROOT/'developer_case_review.json',{'scope':'Targeted developer evidence review, not final human gold adjudication. No labels or runtime decisions changed.', 'cases':cases})
    print('Reviewed '+str(len(cases))+' decisive source/matcher cases; no API calls')

if __name__=='__main__':run()
