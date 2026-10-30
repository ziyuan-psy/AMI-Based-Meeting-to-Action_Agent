"""Derive a local four-meeting auditor without modifying the retained 16-meeting audit."""
from pathlib import Path
from make_driver import replace

ROOT=Path(__file__).resolve().parent
def main():
    out=ROOT/'audit.py';assert not out.exists()
    s=(ROOT.parent/'s2_dev16/audit.py').read_text(encoding='utf-8')
    s=replace(s,"('audit.py','diagnose_selection.py')","('audit.py','replay.py')")
    s=replace(s,"require(not meta['code']['dirty_source_files'],'dirty production source',mid)",
        "require({row.split()[-1] for row in meta['code']['dirty_source_files']}==set(study['source_patch_paths']),'unexpected production source changes',mid)")
    s=replace(s,'requested=16;completed=',"requested=len(study['meetings']);completed=")
    s=replace(s,"'ALL_16_COMPLETED' if completed==16", "'ALL_REQUESTED_COMPLETED' if completed==requested")
    s=replace(s,'# 十六场开发会议：空缓存完整 S2 回归','# 选择重复条目元数据修复：TS3006 四场空缓存 S2 回归')
    s=replace(s,'固定版本：`{study[\'commit\']}`；分支 agent-development。未 commit，未修改生产代码、UI 或标注文档。',
        '基于 `{study[\'commit\']}`，仅修复 item_selection.py 的重复项元数据处理，补丁见 source.patch；分支 agent-development。未 commit，UI 和标注文档未改。')
    s=replace(s,'范围：ES2011、IS1008、TS3004、TS3006，各 a→d。','范围：TS3006 a→d。')
    marker="    diagnosis=load(root/'selection_failure.json') if (root/'selection_failure.json').exists() else None"
    s=replace(s,marker,'''    preservation=load(root/'offline/before.json')
    old=Path(study['original_cohort'])
    require(sha(old/'manifest.json')==preservation['previous_manifest_sha256'],'old manifest changed')
    for rel,digest in preservation['previous_artifact_sha256'].items():require(sha(old/rel)==digest,'old regression artifact changed: '+rel)
    for rel,digest in preservation['previous_parent_files'].items():require(sha(REPO/rel)==digest,'old regression driver/doc changed: '+rel)
    replay=load(root/'offline/replay.json')
    require(replay['result']=='PASSED' and replay['actual_new_api_calls']==0,'offline replay not passed')
    require(replay['after_source_sha256']==sha(REPO/'agent/src/mta/item_selection.py'),'replay/fresh-run source mismatch')
    tests=load(root/'tests.json');require(tests['passed']==400,'tests not passed')
    for rel,digest in tests['test_sha256'].items():require(sha(REPO/rel)==digest,'tested file changed: '+rel)
    require(sha(REPO/'agent/tests/fixtures/selection_missing_reason.json')==tests['fixture_sha256'],'test fixture changed')
    require(sha(root/'source.patch')==study['source_patch_sha256'],'source patch changed')
    original=load(old/'study.json')
    require(study['runtime']['config']==original['runtime']['config'],'config differs from original cohort')
    require(study['runtime']['model']==original['runtime']['model'],'model differs from original cohort')
    for rel,digest in study['input_sha256'].items():require(digest==original['input_sha256'][rel],'different original input: '+rel)
    summary['old_artifacts_preserved']=len(preservation['previous_artifact_sha256'])
    summary['offline_selection_replay_passed']=True
    summary['unit_tests']=tests['passed']
    summary['scope_note']='Independent four-meeting cold rerun. Never combined with the old 13/16 cohort into a single-version 16/16 result.'
    summary['issues']=issues
    summary['artifact_audit']='PASSED' if not issues else 'FAILED'
    summary['campaign_result']='ALL_REQUESTED_COMPLETED' if completed==requested and not issues else 'COMPLETION_OR_AUDIT_GAPS'
    diagnosis=None''')
    s=replace(s,"    text+=['','## 原始输入警告','']",'''    text+=['','## 原始失败回放与验证范围','',
        '原 TS3006b 两份选择响应在旧代码下两次失败；修复后第一份响应一次完成选择，第二份原响应也通过 canonical 校验。首次模型请求完全相同，未新增 API 调用，原始参数保持不变。',
        '400 项测试通过。修复只补充明确指向有效保留目标的重复排除元数据 reason；继续执行原有类型、owner、候选来源与 P2 证据验证。模型、配置和预算完全一致，缓存使用单独的元数据版本。',
        '旧十六场回归及 825 份清单产物保持原样。本轮四场独立执行，不拼接为一次同版十六场全部通过；新版本的其他十二场未在本轮重跑。',
        '离线回放和测试见 offline/，新主流程见 run/；两者分别记录。','',
        '## 原始输入警告','']''')
    out.write_text(s,encoding='utf-8')
    print('New auditor written; original audit unchanged')

if __name__=='__main__':main()
