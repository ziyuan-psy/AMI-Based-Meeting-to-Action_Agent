# 冻结开发快照

本目录的 `source/` 是固定源码，`inputs/` 是三场开发输入，`study.json` 记录模型、配置、
输入和源码哈希。开发分支尚未 commit；使用这里的源码快照可避免误用后续工作区改动。
冻结状态和已知问题见上一级 `verification.json`、`REPORT.md`、`human_review.json`。

## 独立运行固定版本

已验证 Python 3.12.14。在本目录创建 Python 环境，安装 `requirements-frozen.txt` 中的依赖。API 凭据只通过环境
提供，不包含在快照中。模型和 endpoint 必须与 study.json 一致（当前为 Portkey gpt-5-nano）。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-frozen.txt
# 在环境中配置 OPENAI_API_KEY；不要把密钥写入源码、日志或发给组员。
.\.venv\Scripts\python run_frozen.py --verify-only
.\.venv\Scripts\python run_frozen.py --output D:/new-development-run
```

输出目录必须不存在。入口校验源码和输入哈希，并应用冻结配置；从空缓存真实运行抽取、
合并、选择、P2、摘要和验证，每场使用原 80 次调用／100 万 token／1,200 秒预算。
这个入口不加载 gold，不复用本次实验的选择响应。在线采样可能与已保存输出不同。

这是三场开发样本的复现入口。最终独立 evaluation 仍由组员按项目的评估文档准备语料、
gold 与评价范围；完整脚本也在 `source/agent/scripts/`。本次开发未运行留出集。

## 已保存的固定输入实验

`after/s2/`：主 JSON、报告、P2 和澄清 sidecar、阶段 trace、原始 checkpoint。
`after/*.requests.jsonl`：实际模型输入。`scoring/`：同类型一对一三票对齐与支持判断。
选择阶段固定复用 final-v5 每场第一次真实响应，按相同请求/schema/model 验证，且将原成本
计入本场预算；P2/摘要在线重跑。该实验的缓存耗时不代表空缓存运行速度。

先前失败与额外错误注入测试保留在上一级各目录，没有拼接不同轮次的成功产物。
错误注入测试不参与真实开发 F1。已知待确认项由 sidecar 保留，不能当成已被人工确认。
