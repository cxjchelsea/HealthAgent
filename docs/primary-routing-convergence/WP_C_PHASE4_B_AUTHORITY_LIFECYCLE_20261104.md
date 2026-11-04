# WP-C Phase 4-B — Pending 授权撤销持久化收口（2026-11-04）

## 新发现与物理依据

基线 `phase4-a-physical-v1.json` 在代码 `915ff403736979afe28010dba4f5e94901b2f966` 下 **4/4 PASS**：任务创建成功后授权 `consumed`、1 条任务与 1 条成功回执；撤销的确零任务、零回执，但旧 `PendingWriteAuthority.status=pending`，并未真正吊销；MCP 查询仅一条只读回执。此证据的历史裁决保持原样，不回填或覆盖。

`pending_write_authority_for_origin` 基于最近 Turn 的 `needs_input` 状态，正常 API 撤销之后不再引用旧授权；然而底层 `consume_pending_task_write_v1` 的前提仅包括**原始** partial Turn、来源摘要、单次 pending CAS 以及一个新的 running Turn。原始授权状态若保持 `pending`，拿到合法旧 ID 的底层调用有潜在二次消费路径。因此“正常 API 不再展示旧请求”不等于“数据库授权已失效”。

## 本轮实现的限制性修复

- 在 `services/primary_pending_write.py` 新增 `revoke_pending_task_authority_in_session`：基于同一个原始 `conversation_id / origin_turn_id / authority_id / tool_name=create_task / status=pending / consumed_turn_id IS NULL` 做单次 CAS，结果变成 `revoked`。CAS 失败时抛错而不是当作成功撤销；不产生任务、不写入成功工具回执、不把取消标记为“已消费”。
- 在 `apps/api/main.py` 的**Turn 原子终结事务内**，只有被 Host 精确绑定的原始 Pending 写授权且本轮明确撤销才调用该 CAS；事务回滚时撤销和 Turn 状态共同回滚。Primary 原始 route_kind 和 Host disposition 不被改写，也不增加第二个语义所有者。对于原本已成功创建任务的正常标题续答，原始 consumed 授权不参与撤销。
- 底层 `consume_pending_task_write_v1` 仅接受 `status=pending`，由此自动拒绝已 revoked 的旧授权。新增完整 HTTP + 数据库物理行审计模拟“模型错误把撤销判成 clarify_response”，核验无任务、无 Attempt，授权 `revoked`；再绕过普通路由，通过**新的 running Turn + 旧的合法授权 ID** 直接调用执行器，必须得到 `pending_write_authority_conflict` 且零效果。
- 更新 `evals/primary_wp_c_phase4_a_physical.py` 的撤销数据库验收从 `pending` 升级为 **`revoked`**，更新相应正、反例测试。旧 v1 JSON 不追溯变更。

**严格边界**：本轮仍使用范围狭窄的显式撤销检测，仅保证被识别为撤销的请求不会留下可复用的 Pending 写授权。委婉撤销、混合语义、旧授权存在但没有识别为撤销的情况仍需独立挑战和确认协议。尚未证明跨进程并发真实模型与 PostgreSQL 故障注入安全。

## 精确验收命令

本工作包应在全新隔离的 SQLite 数据库中重跑，不能沿用历史 v1 数据库（它的旧授权合理地仍为 pending）：

```powershell
Set-Location D:\HealthAgent
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
git rev-parse HEAD
```

启动 API 窗口（沿用经验证的 Ollama Proxy、Qwen3:14b、Unified、guarded、structured 环境变量）：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-b-revocation-v2.sqlite"
if (Test-Path $dbPath) { throw "database exists: use a new disposable filename" }
$env:DATABASE_URL = "sqlite:///" + ($dbPath -replace '\\','/')
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

另一窗口（与 API 使用相同**绝对路径**）：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-b-revocation-v2.sqlite"
python -m evals.primary_wp_c_phase4_a_physical `
    --endpoint http://127.0.0.1:8012 `
    --sqlite "$dbPath" `
    --output artifacts/wp-c/phase4-b-revocation-v2.json `
    --timeout 240
```

目标 4/4 PASS，且取消组 `authority_statuses=["revoked"]`，`task_count=0`、`attempt_count=0`。要求同时保留 Task 成功组 `consumed`、MCP 组成功查询，避免因收紧授权而将正常功能全部拒绝。

## 仍未完成

现有 pytest/PostgreSQL CI 仅覆盖其各自被执行的范围；本机 Qwen 后的 SQLite `revoked` 状态需独立物理运行，Phase 4-C/D 还需 PostgreSQL 并发与崩溃恢复证据。AC4/AC5 仍 HOLD，main 不合并。
