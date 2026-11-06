# Phase 4-E — 全新真实模型挑战集冻结合同（2026-11-04）

## 目标与冻结边界

本工作包只新增评测器，**不修改任何 Primary Prompt、Host、AgentLoop 或执行器**。本次挑战集首次落库提交：`59f9f6ffb13ed715ed598af012224e97a1511dec`，脚本 `evals/primary_wp_c_phase4_e_unseen.py`，`CASES` 是被冻结的输入与场景顺序，`ROUTES` 和 `assess` 是预先定义的裁决规则。脚本执行时记录 case manifest SHA-256 和代码 HEAD；实际 Qwen 执行前不能先看回答再改变通过条件。

这批用语不复制旧 `primary_wp_c_phase3_live` / `primary_wp_c_independent_challenges` 的原始句子。目标是对未用于这些 Prompt 修复的输入进行一次**首轮泛化测试**。一旦首次执行，不论结果如何，它立即变成已知回归集；后续修改再测不能继续叫作“未见样本”。历史失败不能被覆盖或抹去。

## 冻结矩阵

- plan_a：新建两步计划（只保存）→ FIRST（第二步保留 pending）→ RESUME（全部完成）。
- plan_b：新建两步计划（只保存）→ REVISE（仅替换第二步表达式）→ CANCEL（删除未执行计划）。
- task：当前会话直接创建一次合成任务 → 原 request_id 重放（HTTP 409、0 模型/工具、数据库记录数不增）。
- mem_no / mem_yes：合成键值持久记忆，分别不授权与当轮明确授权；要区分 Host deny 与真实成功写入。
- pending_task：缺标题澄清→补充标题创建任务→同 request_id 重放；授权从 pending 转 consumed，一条任务一条成功 receipt。
- pending_cancel：缺标题澄清→明确撤销；授权必须物理变为 revoked、零 Task、零 Attempt，不能仅依赖 trace 无工具的表象。
- pending_mcp：缺术语澄清→补术语查询；只允许一条成功的 mcp_reference_lookup Attempt，不得新增任务。
- no_plan：无已保存计划时请求继续；route=plan_missing / Host=restrict，不能执行工具。

以上 **18 项业务请求 + 1 项 preflight**。错误场景不得因为“统一拒绝一切”而算 PASS。单业务 Turn 要有一次 Primary、精确 v1 route/evidence、Host disposition；重放不产生新的 durable Turn 或其他效果。脚本通过独立 `sqlite3 mode=ro` 读取相同隔离数据库中 Turn、PrimaryRouteEvidence、PendingWriteAuthority、ConversationTask、AgentAttempt 的逐会话数量及状态，报告不保存原话、模型回答、任务名称、记忆值或完整工具参数。

## 第一轮运行约束

1. 在测试前固定整改分支 HEAD 与测试脚本，不进行 Prompt 调参。
2. 从新的、可丢弃 SQLite 数据库启动本地 API；模型必须 `ollama_proxy/qwen3:14b`，路由必须 Unified / guarded / structured；HTTP 仅允许 localhost。
3. 单独运行脚本（完整执行，不传筛选参数）。所有失败原样存档，不降低冻结条件；失败按模型语义、产品逻辑、评分器缺陷、环境和证据问题分类。
4. 记录精确 HEAD、`manifest_sha256`、PASS/FAIL/BLOCKED、业务路由与数据库副作用；必须 19/19 PASS 才说明**本挑战集**通过，不等于任意表达安全。
5. AC2 旧 Reviewer 函数级与调用图终审仍独立开展；本评测器模型调用类别并不替代十三个 Python 函数 fail-on-call 的静态与运行时验收。

## 操作命令

PowerShell 窗口 1（请停止旧 8012 服务，保留已验证的 Ollama Proxy 连接配置）：

```powershell
Set-Location D:\HealthAgent
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
.\.venv\Scripts\Activate.ps1
$env:MODEL_PROVIDER = "ollama_proxy"
$env:MODEL_NAME = "qwen3:14b"
$env:ROUTING_PROFILE = "unified"
$env:ROUTE_ADMISSION_MODE = "guarded"
$env:UNDERSTANDING_STRATEGY = "structured"
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-e-unseen-v1.sqlite"
if (Test-Path $dbPath) { throw "database exists: use another fresh name" }
$env:DATABASE_URL = "sqlite:///" + ($dbPath -replace '\\','/')
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

窗口 2：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-e-unseen-v1.sqlite"
python -m evals.primary_wp_c_phase4_e_unseen `
  --endpoint http://127.0.0.1:8012 `
  --sqlite "$dbPath" `
  --output artifacts/wp-c/phase4-e-unseen-v1.json `
  --timeout 240
```

上传原始 `artifacts/wp-c/phase4-e-unseen-v1.json`。结果归档之后再作独立审查；在通过之前 AC5/Phase 4-E 仍 HOLD，禁止合并 main。
