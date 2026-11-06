# Phase 4-E — Pending 主语义独立对照验收（2026-11-04）

## 证据基线与整改范围

- `phase4-e-final-head-live-v1.json` 于 `8d068387`：真实 Qwen 8/9，改话题错误落成 `clarify_response / allow`、任务写入成功，是权限事故样本。
- `phase4-e-final-head-live-v2.json` 于 `e6d735f5`：8/9，改话题仍错误 `clarify_response`，但 Host `restrict`，零 Task、零 Attempt、授权 revoked；证明**已有具体样本写入围栏**，并未证明正确语义。
- 本次仅改变 `agent/primary_route_v1.py` 中的 Primary 提案合同，不修改 Host 权限范围和 Pending 执行器。补充先理解当前用户消息、再判断是否续答的语义优先级和对照例子；不采用关键词规则，不把任何特定路由写成硬编码。
- 原先 first-run 和两个 final-head 原始报告均保留，不覆盖。

## 独立对照

`evals/primary_wp_c_pending_semantic_contrast.py` 是新独立测试，使用与历史失败样本不同的文本。共六组对照，两轮一组，加 preflight 共 13 项：纯标题、含负面词的合法标题、信息解释新请求、无标点的信息解释新请求、明确转向解释、拒绝创建。每组既检查 `route_kind` 是否符合预期，也独立检查数据库授权终态、Task 和 Attempt；报告两个独立失败计数，不允许以 Host 安全限制代替主语义正确。模型真实调用必须是 `ollama_proxy/qwen3:14b`、Unified，Primary 单一调用，无旧 Reviewer 二次分类。

## 本地物理测试

窗口一启动 **全新** SQLite、仅本地监听的 FastAPI（端口 8012）；用户应先停止旧的 API 进程；保留曾可用的 Ollama Proxy URL、模型凭据，不将私密信息写入报告。

```powershell
Set-Location D:\HealthAgent
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
git rev-parse HEAD
.\.venv\Scripts\Activate.ps1
$env:MODEL_PROVIDER = "ollama_proxy"
$env:MODEL_NAME = "qwen3:14b"
$env:ROUTING_PROFILE = "unified"
$env:ROUTE_ADMISSION_MODE = "guarded"
$env:UNDERSTANDING_STRATEGY = "structured"
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-e-pending-contrast-v1.sqlite"
if (Test-Path $dbPath) { throw "Do not reuse a physical test DB" }
$env:DATABASE_URL = "sqlite:///" + ($dbPath -replace '\\','/')
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

窗口二执行：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-e-pending-contrast-v1.sqlite"
python -m evals.primary_wp_c_pending_semantic_contrast `
  --endpoint http://127.0.0.1:8012 `
  --sqlite "$dbPath" `
  --output artifacts/wp-c/phase4-e-pending-contrast-v1.json `
  --timeout 240
```

运行后上传 `phase4-e-pending-contrast-v1.json`。脚本拒绝复用有 Turn 的数据库或覆盖报告。

## 裁决

CI 和新对照全量通过才能继续复跑旧 9 项及 19 项业务矩阵。对照失败则按语义错误 vs 安全错误单独分析，不通过扩大关键词黑名单、静默改写模型提案或降低验收阈值获取 PASS。若短无标点的新请求被错写，应先恢复写入安全边界，再考虑单独明确的授权确认协议。当前 Phase 4-E HOLD，不合并 main。
