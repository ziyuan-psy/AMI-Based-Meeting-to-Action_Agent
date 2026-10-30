"""Adapt the immutable preceding audit for the members repair cohort."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
text=(HERE.parent/'select_reason/audit.py').read_text(encoding='utf-8')
def replace(old,new,count=1):
    global text
    assert text.count(old)==count,(old,text.count(old))
    text=text.replace(old,new)

replace("('audit.py','replay.py','diagnose_consolidation.py')", "('audit.py','replay.py','capture.py')")
start=text.index("    require(sha(old/'manifest.json')==preservation['previous_manifest_sha256']")
end=text.index("    replay=load(root/'offline/replay.json')",start)
text=text[:start]+'''    preserved=0
    for cohort in preservation['cohorts']:
        previous=REPO/cohort['path']
        require(sha(previous/'manifest.json')==cohort['manifest_sha256'],'old manifest changed: '+cohort['path'])
        for rel,digest in cohort['artifact_sha256'].items():
            require(sha(previous/rel)==digest,'old regression artifact changed: '+cohort['path']+'/'+rel)
            preserved+=1
        for rel,digest in cohort['parent_files'].items():require(sha(REPO/rel)==digest,'old regression driver/doc changed: '+rel)
'''+text[end:]
replace("replay['after_source_sha256']==sha(REPO/'agent/src/mta/item_selection.py')", "replay['after_source_sha256']==sha(REPO/'agent/src/mta/extract.py')")
replace("tests['passed']==400", "tests['passed']==428")
replace("    require(sha(REPO/'agent/tests/fixtures/selection_missing_reason.json')==tests['fixture_sha256'],'test fixture changed')",
        "    for name,digest in tests['fixture_sha256'].items():require(sha(REPO/'agent/tests/fixtures'/name)==digest,'test fixture changed: '+name)")
replace("summary['old_artifacts_preserved']=len(preservation['previous_artifact_sha256'])", "summary['old_artifacts_preserved']=preserved")
replace("summary['offline_selection_replay_passed']", "summary['offline_merge_members_replay_passed']")
replace("    summary['new_consolidation_failure']=load(root/'consolidation_failure.json') if (root/'consolidation_failure.json').exists() else None\n", "")
replace("    summary['selection_failure_diagnosis']=diagnosis\n", "")
replace("Independent four-meeting cold rerun. Never combined with the old 13/16 cohort into a single-version 16/16 result.",
        "Independent four-meeting cold rerun. Prior 13/16 and 3/4 cohorts remain immutable; never combined into one single-version 16/16 result.")
replace("    original=load(old/'study.json')", "    original=load(old/'study.json')\n    require(study['source_sha256']['agent/src/mta/item_selection.py']==original['source_sha256']['agent/src/mta/item_selection.py'],'prior selection repair changed')")
replace("    summary['duplicate_reason_metadata_count']=", "    summary['merge_diagnostics']={r['meeting_id']:r.get('merge_diagnostics',{}) for r in rows}\n    summary['duplicate_reason_metadata_count']=")
replace("        if result['status']!='COMPLETED':", '''        merge_attempts=[e for e in events if e.get('event')=='attempt' and e.get('stage') in ('consolidate','decision_consolidate') and 'sample' in e]
        rejects=[];rejection_counts=Counter();unusable=0
        for attempt in merge_attempts:
            if not attempt.get('items'):unusable+=1
            raw=next(e for e in attempt['events'] if e['event']=='raw_groups')
            merged=next(e for e in attempt['events'] if e['event']=='merged')
            invalid=set()
            for rejection in merged.get('rejected',[]):
                rejection_counts[rejection['reason']]+=1
                invalid.add(rejection['group_index'])
                rejects.append({'trace_seq':attempt['seq'],'stage':attempt['stage'],'sample':attempt['sample'],
                    'attempt':attempt['attempt'],**rejection,'kept_items':len(merged['items'])})
            for lineage in merged['lineage']:
                require(lineage['group_index'] not in invalid,'rejected group partly accepted',mid)
                group=raw['groups'][lineage['group_index']]
                require(isinstance(group['members'],list) and all(type(i) is int for i in group['members']),
                        'noninteger members accepted',mid)
        logical=lines(requests) if requests.exists() else []
        corrections=sum(r['stage'] in ('consolidate','decision_consolidate') and any(
            'Correction: previous response had no usable groups (' in str(m.get('content','')) for m in r['messages']) for r in logical)
        row['merge_diagnostics']={'logical_attempts':len(merge_attempts),'unusable_attempts':unusable,
            'correction_requests':corrections,'rejection_counts':dict(rejection_counts),'rejections':rejects}
        if result['status']!='COMPLETED':''')

start=text.index("    text=['# 选择重复条目元数据修复")
end=text.index("    (root/'REPORT.md').write_text",start)
text=text[:start]+'''    text=['# 合并 members 类型修复：TS3006 四场空缓存 S2 回归','',
        f"基于 `{study['commit']}`，分支 agent-development。生产改动为 extract.py 的合并输入校验；保留上一轮 item_selection.py 修复。补丁见 source.patch。未 commit。",'',
        'TS3006 a→d 从 TXT 真实运行，新目录、新缓存；只使用本轮产生的更早会议历史。模型、配置、预算、初次合并提示词及 schema 不变；不可用响应重试时增加格式纠正信息。未读取 gold 或留出集。','',
        f"完成 **{completed}/{requested}**；产物审计 **{summary['artifact_audit']}**。每场只运行一次，失败则按原生策略阻塞同系列后续会议，不替换旧输出或重跑择优。",'',
        '| 会议 | 状态 | 秒 | API 尝试 | 计入 token | 可记录/P2 输入 | 澄清 | 发布关联 |',
        '| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |']
    for r in rows:
        text.append(f"| {r['meeting_id']} | {r['status']} | {r.get('seconds','—')} | {r.get('api_attempts',0)} | {r.get('charged_tokens','—')} | {r.get('recordable','—')}/{r.get('p2_items','—')} | {r.get('clarification_requests','—')} | {r.get('published_links','—')} |")
    text+=['',f"总 API 尝试 {totals['api_attempts']}；已知 token {totals['known_tokens']:,}，计入 token {totals['charged_tokens']:,}，其中未知用量保守预留 {totals['unknown_usage_tokens']:,}。计入量不等于供应商账单。",'',
        '## 原始失败响应回放','',
        '三个真实合并响应逐一交给旧/新 _merge_groups，零 API 调用：原 attempt 17 在旧代码触发 unhashable type: dict；新代码拒绝两组对象成员并保留拒绝原因。attempt 18 的 11 个有效条目完全不变；attempt 19 仍为空。原始输入不改写。',
        '回放只验证实际捕获响应，不伪造修复后的模型重试。428 项测试通过（67.49 秒）；包含混合成员整组拒绝、有效同批样本保留、原重试上限、共享调用预算及缓存原响应重校验。','',
        '## 修复边界','',
        '- 先验证数组、分组和成员类型，再进行去重；members 只允许候选整数编号，拒绝对象、字符串、布尔值等。拒绝整组，不猜测或保留其中部分成员。',
        '- 同批有效分组/样本继续合并；无可用分组时原每样本最多 3 次重试增加格式纠正。没有增加采样数、请求重试、总调用或 token 上限。',
        '- 所有样本无效仍明确失败；不回退为未经合并的原始候选。运行预算/异常仍向上退出，已有 checkpoint、原响应和 partial 保留。',
        '- 有原响应的缓存先重新验证，失效样本才在原统一预算内补齐；本轮使用全新空缓存。','',
        '## 本轮自然触发的合并诊断','',
        '| 会议 | 合并逻辑响应 | 无可用响应 | 格式纠正请求 | 非整数成员分组 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for r in rows:
        d=r.get('merge_diagnostics',{})
        text.append(f"| {r['meeting_id']} | {d.get('logical_attempts',0)} | {d.get('unusable_attempts',0)} | {d.get('correction_requests',0)} | {d.get('rejection_counts',{}).get('members_must_be_candidate_indexes',0)} |")
    text+=['','在线未自然触发的错误路径，以原响应回放和测试为验证依据，不声称本轮在线发生过。逐组拒绝原因及 trace 序号见 summary.json。','',
        '## 阶段调用分布','',
        '| 会议 | 抽取 | 合并 | Decision 重合并 | 选择 | P2 | 自主工具决策 | 摘要 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        counts=r.get('model_attempts_by_stage',{})
        text.append('| '+r['meeting_id']+' | '+' | '.join(str(counts.get(k,0)) for k in ('extract','consolidate','decision_consolidate','select','p2_check','agent','abstract'))+' |')
    text+=['','## 产物、预算及来源检查','',
        '- 完成场次校验最终 JSON/schema、P2 与澄清对应、报告问题覆盖、文件哈希、阶段/Agent trace、TXT 解析 checkpoint 和实际调用/token 账本。',
        '- 每场 80 次调用、1,000,000 token；请求 300 秒，整场 1200 秒，并发 4，传输重试 2。逻辑 requests 与 runtime_control 实际 API 尝试分别记录。',
        f"- P2 输入 {totals['p2_items']}，accepted {totals['accepted']}、pending {totals['pending']}、可记录 {totals['recordable']}；澄清 {totals['clarification_requests']} 条；自然有限修正 {totals['valid_revisions']} 次，见 limited_revisions.json。",
        f"- 发布关联 {totals['published_links']} 条；旧记录→工具返回→Agent 选择→新记录见 [关联清单](CROSS_MEETING.md) / cross_meeting_links.json。未决关系见 pending_relations.json。只允许同系列更早会议；关系语义尚未新增人工确认。",
        f"- 两轮旧回归共 {preserved} 份清单产物及其父级驱动/文档的哈希保持原样；UI/标注文档等 {len(protected)} 个受保护文件未变。",
        '- 原 16 场回归仍为 13/16，上一轮 TS3006 为 3/4；本轮四场独立记录，不拼接成同版 16/16。其余十二场未在本轮重新运行。',
        '- 本次属于开发回归及结构/来源核查，没有计算 P/R/F1，也不作为稳定性或最终语义正确性的证明。','',
        '## 失败与审计问题','']
    failed=[r for r in rows if r['status'] not in ('COMPLETED','BLOCKED_BY_PRIOR_FAILURE')]
    text += [f"- {r['meeting_id']}：{r['status']}；阶段 {r.get('failed_stage')}；原因 {r.get('reason')}；已有 checkpoint / partial 见 result.json。" for r in failed]
    text += ['- '+i['meeting_id']+'：'+i['error'] for i in issues]
    if not failed and not issues:text.append('无运行失败或产物审计错误；内容正确性仍需独立 evaluation。')
    text+=['','## 输入警告','']
    notes=[(r['meeting_id'],w) for r in rows for w in r.get('parser_warnings',[])]
    text+=['- '+mid+'：'+w['category']+'；'+w['message'] for mid,w in notes]
    if not notes:text.append('原生 TXT 解析 checkpoint 离线复核无警告。')
    text+=['','输入警告按原 parser 保留，未改写原始时间或转写。','',
        '## 复查入口','',
        '- study.json / source/ / inputs/：固定代码、模型、配置、输入及哈希。',
        '- run/s2/*.json、*_report.md、*.p2.json、*.clarifications.json：最终记录和待确认项。',
        '- run/s2/*.stages.jsonl、*.trace.jsonl、_attempts/：原始模型响应、阶段 checkpoint、工具返回与失败原因。',
        '- offline/：原失败回放、测试、修复前哈希；summary.json / verification.json / manifest.json：逐场审计与完整性。','']
'''+text[end:]
(HERE/'audit.py').write_text(text,encoding='utf-8')
print('Created independent auditor; both previous audits unchanged.')
