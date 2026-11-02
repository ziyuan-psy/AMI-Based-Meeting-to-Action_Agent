"""Offline publication of fixed-input quality regression; never calls a model."""
import json
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parent
PRIMARY=ROOT/'release-v9'
def load(p):return json.loads(p.read_text(encoding='utf-8'))

def prf(tp,n,g):
    p=tp/n if n else 0;r=tp/g if g else 0
    return {'tp':tp,'sys':n,'gold':g,'precision':round(p,3),'recall':round(r,3),'f1':round(2*p*r/(p+r),3) if p+r else 0}

def main():
    study=load(PRIMARY/'study.json')
    rows=load(PRIMARY/'comparison.json')['meetings'] if (PRIMARY/'comparison.json').exists() else []
    eligible=[r for r in rows if r['status']=='COMPLETED']
    metrics={}
    for arm in ('teammate','before','before_p2','after'):
        metrics[arm]={}
        for kind in ('actions','decisions','problems'):
            values=[r['arms'][arm]['metrics'][kind] for r in eligible if 'unscored' not in r['arms'][arm]['metrics'][kind]]
            metrics[arm][kind]={**prf(*(sum(v[k] for v in values) for k in ('tp','sys','gold'))),'meetings':len(values)}
    runs=[]
    for mid in study['meetings']:
        path=PRIMARY/'after'/(mid+'.result.json')
        if not path.exists():continue
        r=load(path)
        previous=load(Path(study['before'])/'after'/(mid+'.result.json'))
        p2=load(PRIMARY/'after/s2'/(mid+'.p2.json')) if r['status']=='COMPLETED' else None
        runs.append({'meeting_id':mid,'status':r['status'],'budget':r['whole_meeting_budget'],
            'before_budget':previous['whole_meeting_budget'],
            'recordable':sum(i['recordable'] for i in p2['items']) if p2 else None,
            'items':len(p2['items']) if p2 else None,'states':dict(Counter(i['state'] for i in p2['items'])) if p2 else {},
            'revisions':sum(e.get('outcome')=='revised_requires_recheck' for i in p2['items'] for e in i['history']) if p2 else 0})
    summary={'scope':study['scope'],'primary_case':PRIMARY.name,'metrics':metrics,'runs':runs,
             'human_review':load(ROOT/'human_review.json') if (ROOT/'human_review.json').exists() else {'status':'PENDING'},
             'evaluation_limit':'One development sample per meeting, draft gold and model alignment. Not final evaluation.'}
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 条目质量修复：固定输入回归','',
        '最终策略 ITEM_POLICY_VERSION=5 / P2_PROTOCOL_VERSION=6。输入为上一轮三场冻结抽取候选及真实合并响应；选择阶段复用 final-v5 每场第一次真实模型响应，已核对请求输入、模型、system 和 tool schema 相同。原上游与选择请求的调用/token/耗时先预留，P2 和摘要在线运行。没有按评分挑选响应。','',
        '## 同口径结果','',
        '| 类型 | 队友 P/R/F1 | 修改前 P/R/F1 | 修改后 P/R/F1 |','| --- | --- | --- | --- |']
    def cell(m):return f"{m['precision']:.3f}/{m['recall']:.3f}/**{m['f1']:.3f}** ({m['tp']}/{m['sys']}/{m['gold']})"
    for kind in ('actions','decisions','problems'):
        lines.append('| '+kind+' | '+' | '.join(cell(metrics[a][kind]) for a in ('teammate','before','after'))+' |')
    lines+=['','括号为正确匹配/系统条数/gold。只汇总已完成评分的共同会议；不把失败输出作为零分。参考判断从已冻结评分直接复用，没有重复评判后择优。','',
            '这些是原始模型对齐分数，不能直接宣称超过队友。逐条检查仍发现范围或 actor 可疑匹配，已单独标记在 alignment_review.json，未改原始投票或分数。最终评价应独立复核这些配对。','',
            '## P2 前后分开检查','',
            '| 类型 | 当前进入 P2 前 P/R/F1 | 当前最终 P/R/F1 |','| --- | --- | --- |']
    for kind in ('actions','decisions','problems'):
        lines.append('| '+kind+' | '+cell(metrics['before_p2'][kind])+' | '+cell(metrics['after'][kind])+' |')
    lines+=['','P2 前后一对一匹配也可能受文本列表和模型投票波动影响；逐项 trace 用于归因，不把聚合分差直接解释成 P2 删除了多少正确条目。','',
            '| 会议 | 状态 | 修改前→后尝试 | 修改前→后计入 token | 可记录/输入条目 |','| --- | --- | ---: | ---: | --- |']
    for r in runs:
        lines.append(f"| {r['meeting_id']} | {r['status']} | {r['before_budget']['combined_attempts']} → {r['budget']['combined_attempts']} | {r['before_budget']['combined_charged_tokens']:,} → {r['budget']['combined_charged_tokens']:,} | {r['recordable']}/{r['items']} |")
    lines+=['','计入 token 包含未知失败用量的保守预留，不能视为供应商账单；缓存运行耗时不能当作空缓存全流程加速。','',
        '## 完整保留的失败轮次','',
        '- retention-v1：IS1008a 完成；TS3006a 选择提交缺少字段/结构损坏，明确失败并停止。',
        '- retention-v2：IS1008a 的成员编号和类型来源不一致，校验拒绝并停止。',
        '- retention-v3：固定 C-key 的格式正确，但合理的跨类型重复排除被错误当作整场失败，未继续。',
        '- retention-v4：parse 阶段 PermissionError，实际新增 API 调用为 0；文件随后可正常读取。',
        '- final-v5：前两场完成；第三场因保留/重复建议矛盾被过严校验拒绝。',
        '- release-candidate：仅准备和离线预检，未进行 API 运行；随后补齐源码说明，另建 release-v6。',
        '- release-v6：两场完成，第三场在保存 P2 产物时出现 PermissionError；模型响应及已有 checkpoint 保留。',
        '- release-v7：补齐有上限的 Windows 原子替换重试及错误码诊断，用固定首次选择响应重新运行全部 P2/摘要，不拼接此前成功输出。','',
        '- release-v8：用于明确日期来源检查的冻结与定向测试，没有三场自然案例运行。',
        '- release-v9：最终完整三场运行，加入一次核心语义聚焦复核；每场均完成，保留所有待确认项。','',
        '## 额外保护与人工核对','',
        '错误时间注入使用真实 slides 对话，故意加入 by Friday，属于合成错误，不参加 F1。首轮模型接受了不存在的日期；加入明确星期/ISO 日期来源检查后，相同原始提交的零 API 回放被拦下，见 time_guard_replay.json。第二次在线注入因引用及修正参数问题停留待确认，没有把它宣传为自主修正成功。有效的删除、撤销旧检查和重新评估由单元测试覆盖。',
        '聚焦 assess_core 仅提供一次重新检查机会；模型仍可 stop/clarify，不绕过未读引用、文档缺口或日期来源校验。',
        '人工已确认 slides 任务及时间、UI 技术功能任务、售价/销量/成本三条 Decision 拆分。营销任务的额外核对若尚无回复，仍记录为待确认，不扩大为已完成独立评价。','',
        '## 复查','',
        '- [最终比较](release-v9/comparison.json)、[汇总](summary.json)、[源码和输入](release-v9/study.json)。',
        '- [固定版本独立运行入口与说明](release-v9/README.md)，先运行 run_frozen.py --verify-only 可离线核对。',
        '- after/s2 下保存原始阶段、全部 checkpoint、P2/澄清 sidecar；请求写入 *.requests.jsonl。',
        '- select/completed 记录排除理由、原子事实与成员对应；select/cap 记录最终数量限制。',
        '- P2 修正必须是删除尾部限定，撤销旧检查后重新 assess_item；不能自动放行。',
        '- [可疑对齐诊断](alignment_review.json)、[全部开发轮次实际用量](campaign_usage.json)、[用户人工核对](human_review.json)。历史预留成本没有重复当成新增 API 消耗。',
        '- 开发 gold 和三票模型匹配尚不能替代组员的最终独立 evaluation。',
        '- 人工核对和冻结状态见 human_review.json / verification.json。未经人工回复不得宣称审核或冻结通过。',
        '- [协议与复现说明](../../docs/ITEM_QUALITY_REPAIR.md)。','']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'eligible_meetings':[r['meeting_id'] for r in eligible],'metrics':metrics,'runs':runs},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
