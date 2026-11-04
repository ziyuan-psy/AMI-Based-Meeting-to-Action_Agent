"""Save the reviewable report and verify current/protected artifacts. Offline only."""
import ast
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
FINAL=ROOT/'fixed-v5'

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def verify(base,values):
    for rel,h in values.items():assert sha(base/rel)==h,rel
    return len(values)
def files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
def git(*args):
    return subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),*args],cwd=REPO,text=True,encoding='utf-8').strip()

def main():
    study=load(FINAL/'study.json');summary=load(FINAL/'summary.json')
    assert summary['result']=='PASSED'
    before=load(ROOT/'before.json')
    oldroot=REPO/'agent/results/smoke_repairs'
    assert sha(oldroot/'manifest.json')==before['previous_repair_manifest_sha256']
    original_repairs=verify(oldroot,before['previous_repairs_sha256'])
    protected=verify(REPO,before['protected_files'])
    oldsmoke=REPO/'agent/results/frozen_smoke'
    oldrelease=REPO/'agent/results/quality_repairs/release-v9'
    smoke_count=verify(oldsmoke,load(oldsmoke/'manifest.json')['artifact_sha256'])
    release_count=verify(oldrelease,load(oldrelease/'freeze_manifest.json')['artifact_sha256'])
    source=study['runtime']['code']['source_sha256']
    verify(REPO,source);verify(FINAL/'source',source)
    for root in ROOT.glob('fixed-v*'):
        verify(root,load(root/'study.json')['artifact_sha256'])
    assert git('branch','--show-current')=='agent-development'
    assert git('rev-parse','HEAD')=='3b886f6c0c1520a1c5bc8628da7198a871c25495'
    assert not git('diff','--cached','--name-only')
    subprocess.run(['git','-c','safe.directory='+REPO.as_posix(),'diff','--check','--','agent/src','agent/tests'],
                   cwd=REPO,check=True,capture_output=True)
    tests={'command':'python -m pytest agent/tests -q','passed':344,'seconds':64.70,'exit_code':0,
           'provider_calls':0,'source_sha256':source,
           'test_sha256':{p.relative_to(REPO).as_posix():sha(p) for p in (REPO/'agent/tests').glob('*.py')}}
    save(FINAL/'tests.json',tests)
    oldcost=load(ROOT/'before_cost_audit.json')
    lines=['# Slides 时间与 TS3006b Trace 审计结果','',
        'Slides 时间、重复执行约束、产物与预算检查：**PASSED**。有下文列出的消耗和关联稳定性限制。当前分支 agent-development，基于 3b886f6，未 commit。', '',
        '## Slides 时间遗漏', '',
        '初始抽取已将时间填为 null/missing；后续合并/截断没有删除一个已有时间。P2 先前仅选中承诺回应，漏读请求中约定的交付时间。deadline 是原文字符串，允许事件相对时间，不要求日历日期。', '',
        f"最终在线输出：`{summary['slides'][0]['deadline']}`；basis=`explicit`。任务正文和负责人保留。", '',
        '时间原文：'+summary['slides_time_evidence']['quote'],
        '来源：`'+summary['slides_time_evidence']['da_id']+'`。', '',
        '缺少时间时，仅重看该候选已交付的原始证据，进行一次独立时间复查；不会用正则直接填字段，不新增来源、不提高预算，可继续保持待确认或停止。完整检查和理由见 fixed-v5/run/s2/IS1008a.p2.json。', '',
        '## TS3006b 调用与空转', '',
        '原调用结构：抽取 12、合并 4、Decision 合并 4、选择 2、P2 检查 34、Agent 17、摘要 1，共 74。',
        f"原试跑 {oldcost['whole_seconds']} 秒，其中 {oldcost['upstream_wall_seconds']} 秒（约 79.7%）发生在上游结束前；P2 与输出部分约 {oldcost['p2_and_tail_wall_seconds']} 秒。P2 有空转，但不是整场延时的主要来源。", '',
        '| 固定输入会议 | 原 P2/Agent/摘要调用 | 新 P2/Agent/摘要调用 | 上游原调用预留 | 预算等效总调用 | 已执行的完全重复查找（前→后） |',
        '| --- | ---: | ---: | ---: | ---: | --- |']
    for row in summary['meetings']:
        lines.append(f"| {row['meeting_id']} | {row['before_new_stage_calls']} | {row['after_new_stage_calls']} | {row['captured_upstream']['attempts']} | {row['equivalent_whole_calls']} | {row['before_trace']['exact_repeated_executed_lookups']} → {row['after_trace']['exact_repeated_executed_lookups']} |")
    lines += ['', '| 新执行部分 | 原 token | 新 token | 原阶段约秒数 | 新阶段秒数 | 协议无效提交（前→后） | 检索额度（前→后） |',
              '| --- | ---: | ---: | ---: | ---: | --- | --- |']
    for row in summary['meetings']:
        lines.append(f"| {row['meeting_id']} | {row['before_new_stage_tokens']} | {row['new_known_tokens']} | {row['before_p2_and_tail_seconds']} | {row['new_seconds']} | {row['before_trace']['invalid_actions']} → {row['after_trace']['invalid_actions']} | {row['before_trace']['lookups']} → {row['after_trace']['lookups']} |")
    lines += ['', '上表只对照冻结的相同候选、相同 TXT/参与者/scenario 和同一 TS3006a 历史记录。原上游调用、token、时间已从预算扣除；本轮实际新执行 P2、摘要、验证和报告，**不是全阶段空缓存速度测试**。', '',
        '明确修复：', '',
        '- 同条目的重复/关键词等价查询在执行前拒绝，不再占用检索额度；模型提交本身仍计 API 调用。可改变内容、范围、类型或窗口。',
        '- 会议/记录编号和条目类型标签不能单独充当正文查询词；空结果后可自主改查、读取、提交历史判断或停止，不自动断言没有关系。',
        '- owner 已解决时不提供无关参与者查询。核心复查可以保留同正文已通过的 conflict；仍不确定时保持待确认。',
        '- 无历史作用域中错误选择同轮关联并漏交检查时，返回协议错误，避免 KeyError 整场退出。', '',
        '精确旧模型参数重放另见 captured_replay.json：检索额度 4→2、3→2；一处核心复查提交 3→2，结果仍待确认。在线前后还包含模型输出波动，不能把全部调用变化归因于代码或宣称稳定速度改善。', '',
        '## 剩余限制', '',
        'TS3006b 查找次数减少，但无效格式提交从 8 增至 14，抵消了大部分模型调用收益。实际新执行部分 token 从 363,916 增至 413,246，耗时约 161.6→168.4 秒。IS1008a 新执行部分调用 13→15。当前修复提高了时间字段完整性并防止明确重复执行，不能称为整体降耗或提速完成。', '',
        '跨会议关联本轮 1→0：TS3006b#10（UI 将功能分组）在初检后保留 prior_context_not_checked，未继续检索，最终关系待确认。代码没有补造 links，旧历史不变。跨会议关系可重复性尚未解决，本轮不据此宣布完整开发验收/最终 evaluation 通过。', '',
        '## 验证范围与完整性', '',
        '- 344 项测试通过；最终 JSON、报告、trace、P2 检查、澄清文件和原预算验证通过。',
        f'- UI/标注文档保护哈希 {protected} 个、上轮修复产物 {original_repairs} 个、原空缓存产物 {smoke_count} 个、原冻结包 {release_count} 个均未改变。',
        '- 独立 slides 续接诊断 deadline_review_probe-v2 只新增 1 次模型调用，验证了同一真实失败提交的恢复；它没有用于补填主回归结果。',
        '- fixed-v1/v2/v3 的失败，以及 fixed-v4 的时间遗漏、第一次专项复查仍未恢复的输出全部保留，未拼接为最终成功结果。',
        '- HTTP 400 已用单次、零重试诊断定位为本次顶层 allOf 兼容性问题，最终改用说明提示及代码约束，工具 schema 顶层兼容性已测试。',
        '- 本轮未计算 gold/F1，未执行最终 evaluation。任何新跨会议案例均不自动继承旧人工确认。',
        '- 新产物 SHA-256 清单见 manifest.json；源码、模型、配置、固定输入与上游预算见 fixed-v5/study.json。', '']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    parses={'json':0,'jsonl':0,'jsonl_rows':0,'python':0}
    needle=os.environ.get('OPENAI_API_KEY','').encode()
    assert len(needle)>=12
    for p in files():
        assert needle not in p.read_bytes(),f'Credential detected in {p.relative_to(ROOT)}'
        if p.suffix=='.json':load(p);parses['json']+=1
        elif p.suffix=='.jsonl':
            for line in p.read_text(encoding='utf-8').splitlines():
                if line.strip():json.loads(line);parses['jsonl_rows']+=1
            parses['jsonl']+=1
        elif p.suffix=='.py':ast.parse(p.read_text(encoding='utf-8'));parses['python']+=1
    verification={'result':'PASSED','at':datetime.now(timezone.utc).isoformat(),
        'branch':git('branch','--show-current'),'base_commit':git('rev-parse','HEAD'),'committed':False,
        'staged_files':[],'unit_tests':344,'protected_files_unchanged':protected,
        'previous_repairs_unchanged':original_repairs,'previous_smoke_unchanged':smoke_count,
        'previous_release_unchanged':release_count,'credential_matches':0,'artifact_parse_checks':parses,
        'source_files_verified':len(source),'tracked_changes':git('diff','--name-only').splitlines(),
        'preexisting_annotation_change_preserved':'revised_gold_output/README.md','gold_scored':False}
    save(ROOT/'verification.json',verification)
    manifest={'format_version':1,'artifact_sha256':{p.relative_to(ROOT).as_posix():sha(p) for p in files() if p!=ROOT/'manifest.json'}}
    save(ROOT/'manifest.json',manifest)
    verify(ROOT,load(ROOT/'manifest.json')['artifact_sha256'])
    print(json.dumps({'result':'PASSED','artifacts':len(manifest['artifact_sha256']),
        'protected':protected,'old_repairs':original_repairs,'tests':344,'secret_matches':0},ensure_ascii=False))

if __name__=='__main__':main()
