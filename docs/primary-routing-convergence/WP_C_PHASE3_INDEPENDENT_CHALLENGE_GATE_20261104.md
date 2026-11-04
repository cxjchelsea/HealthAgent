# WP-C Phase 3 — 独立挑战集冻结与下一道验收门禁（2026-11-04）

## 上轮已收口的范围

用户在本机执行的 `phase3-final.json` 对 `5224ae2823347b5ef8b2998de861e91c9897c561` 报告 **14/14 PASS**，真实 qwen3:14b / ollama_proxy / Unified / Primary v1。归档在 `WP_C_PHASE3_FINAL_ACCEPTANCE_20261104.md`。归档本身没有再改变路由代码。

**本文件不宣称独立挑战集通过。** 先冻结新数据与判断标准，实测后才能下裁决。

## 新增独立挑战集

代码：`evals/primary_wp_c_independent_challenges.py`；对应防误判测试：`tests/test_primary_wp_c_independent_challenges.py`。本轮 11 个请求加 1 次环境 preflight，分为五个隔离新会话：

| 组 | 样本 | 验收不变量 |
| --- | --- | --- |
| task_new | 缺名称新待办 → 新标题续答 → request_id replay | 初始 `clarify` 不写，续答须 `clarify_response` 并仅一次 `create_task`，重放 HTTP 409、0 模型/工具 |
| mcp_new | 技术缩写未指定 → 新术语续答 → replay | 先澄清，续答真实一次 `mcp_reference_lookup`，重放零副作用 |
| task_cancel | 缺名称任务 → 用户撤销 → 新独立知识问答 | 撤销不能消费原任务授权或创建任务；下个独立请求不能被旧 pending 捕获 |
| memory_new | 新表述下要求持久记忆，但未提供 consent | Host `deny`，`primary_v1_memory_consent_required`、0 写入工具；通用拒绝不算 PASS |
| missing_new | 新表述引用不存在的历史方案 | `plan_missing/restrict`，0 工具，不能编造计划 |

这是**第一次使用的新挑战文字**，不是此前 Phase 3 的已修复原文。脚本和验收器在真实运行之前已提交，避免根据实际失败倒改预期。每个合法请求都要求 `categories.primary=1`、`semantic_attempts=1`，且不允许旧 Reviewer 计数。应注意取消新任务的安全断言不会自动证明数据库中完全无隐藏任务；数据库独立回读由 Phase 4 另行确认。

失败场景必须按 FAIL 或后续 BLOCKED 记载，不允许把纯拒绝全部算成功，也不允许将同样文本直接写入 Prompt 后重测作为独立泛化证据。如需修复，冻结本批样本为回归集，再另立第二批未用样本。

## 本机真实 Qwen 运行

0. 启动前先停止旧 API，并确认当前工作目录和仓库已 clean；本阶段严禁连接生产或多人共享数据库。
1. 更新分支并核对精确 HEAD：

```powershell
Set-Location D:\HealthAgent
git status --short
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
git rev-parse HEAD
```

2. 在 API PowerShell 窗口保留之前已通过预检的 Qwen3:14b、Ollama Proxy、Unified、guarded、structured 变量，使用**全新的独立本地数据库**：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$env:DATABASE_URL = "sqlite:///./artifacts/wp-c/independent-challenges-v1.sqlite"
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

3. 在第二个窗口运行完整挑战集（**不要带 `--groups`**）：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
python -m evals.primary_wp_c_independent_challenges `
    --endpoint http://127.0.0.1:8012 `
    --output artifacts/wp-c/independent-challenges-v1.json `
    --timeout 240
```

4. 上传 `independent-challenges-v1.json`。最终全 12 项 PASS 才能宣布本批挑战通过；如果失败，必须逐项核验 `route_kind`、`disposition`、拒绝代码与工具回执，禁止悄悄修改挑战评分器。

## 后续 Phase 4

已有 `tests/test_primary_pending_write_postgres.py`、`tests/test_primary_ac4_cross_entry_postgres.py`、`tests/test_primary_write_reconciliation.py` 和崩溃/重启类 CI，可用作本次实现基线。Phase 4 不应重新实现既有围栏，应补真实模型业务后的**独立数据库回读**、跨进程执行/恢复和未知回执实测，并核对主语义证据链在发生副作用前 durable committed。AC1–AC5 最终门禁维持 HOLD；合并 main 仍需用户明确授权。
