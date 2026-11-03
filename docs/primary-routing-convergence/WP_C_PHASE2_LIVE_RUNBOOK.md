# WP-C Phase 1 验收归档与 Phase 2 真模型计划生命周期

## Phase 1 已验收（2026-11-03）

用户 Windows 本机 Ollama Proxy/Qwen3:14b 实际运行 `evals/primary_wp_c_live.py`，原始报告 `phase1-live-v3.json`（由用户提交验收，非 CI 生成）。精确 HEAD：`2a504b55520367729e35b08936c813ab60936d3f`。4/4 PASS：

- preflight: Unified、guarded、structured、Primary v1 有效；真实 provider Ollama Proxy / qwen3:14b。
- ordinary_calculate: HTTP 200, completed, calculate true, answer contains 168, durable route_kind=ordinary_tool/disposition=allow, primary HTTP=1。
- missing_plan_no_dispatch: HTTP 200, partial, Host primary_v1_missing_plan_status, route_kind=plan_missing/disposition=restrict, tools=[]。
- create_plan_only: HTTP 200, partial, plan_created_pending_execution, statuses=[pending,pending], tools=[], route_kind=plan_create/disposition=allow。

结果证明**此合成样本集合**可通过；不证明多次重复运行的模型稳定率或全部 AC5。

## Phase 2 范围

脚本：`python -m evals.primary_wp_c_phase2_live`。使用**三个独立新会话**验证：

- 生命周期组：CREATE_A → FIRST（只执行步骤 1）→ RESUME（仅执行剩余步骤）→ REPLAY（原 request_id 再提交）。
- 修改组：CREATE_B → REVISE（第二步 5*6 → 7*8，不执行工具）。
- 取消组：CREATE_C → CANCEL（删除计划，不执行工具）。

预检必须确认 Ollama Proxy + qwen3:14b + Unified + Primary v1 + guarded + structured。每轮检查 Host durable evidence 的 route_kind、disposition、semantic_attempts、旧语义 reviewer HTTP 分类为 0，并将安全诊断写入 JSON。若某组前一步失败，后续**BLOCKED**，不会继续运行不可靠依赖。该阶段不故意杀进程，也不请求记忆/任务写入。

结果不是数据库独立物理回读；后续 Phase 4 应使用独立数据库查询验证 dispatch journal、claims、CAS、恢复和崩溃隔离。不能把此脚本的端到端返回结果视为所有执行持久化约束已通过。

## Windows PowerShell 操作

1. 停止之前运行的 8012 API（Ctrl+C），更新开发分支并确认无本地冲突：

```powershell
Set-Location D:\HealthAgent
git status --short
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
git rev-parse HEAD
```

2. 第一个终端：用 Phase 1 相同的 Qwen3:14b Ollama Proxy / ROUTING_PROFILE=unified / ROUTE_ADMISSION_MODE=guarded / UNDERSTANDING_STRATEGY=structured / M5_UNIFIED_ROUTE_ENABLED=true 环境。保留已确认的 MODEL_BASE_URL，使用**独立** DB：

```powershell
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$env:DATABASE_URL = 'sqlite:///./artifacts/wp-c/live-phase2.sqlite'
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

注意 PowerShell 环境变量只存在于对应进程及其子进程；若使用新终端启动 API，需**重新设置全部 Phase 1 环境变量**，否则可能退回 fake/legacy 运行。只以 GET /model 的实际返回状态作为准入，而非仅凭本地命令。

3. 第二个终端：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
Invoke-RestMethod http://127.0.0.1:8012/model | ConvertTo-Json -Depth 8
python -m evals.primary_wp_c_phase2_live --endpoint http://127.0.0.1:8012 --output artifacts/wp-c/phase2-live.json --timeout 240
```

4. 将 `artifacts/wp-c/phase2-live.json` 上传验收。**失败则保存全部证据、停止该组后续操作，不要人工修改 DB 伪造 PASS。** 若需要复跑，脚本会创建新会话；环境启动后的新数据库应独立于 Phase 1。

**Merge Gate:** Phase 1 PASS；Phase 2/3/4 未证实前 AC5 保持 HOLD。未经用户明确授权不合并主分支。
