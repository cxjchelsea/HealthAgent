# WP-C Phase 4-E — 最终 HEAD 真实 Qwen 回归操作单（2026-11-04）

## 目标与前提

AC2/Pending 本地收口测试 62/62 PASS；PostgreSQL 4/4 PASS；此前 HEAD `1d71be5b` 的 Qwen 已知挑战回归 19/19 PASS。新增 Pending 非续答终态撤销以后，必须对新 HEAD 做一次真实 Qwen 物理检查，才能将其作为最终合并审查证据。

新增独立脚本 `evals/primary_wp_c_phase4_e_final_live.py`，**不是**更改原始 19-case `phase4-e-unseen-v1`，也不是新的首次未见挑战。固定覆盖四条隔离会话：正常 Pending 名称补齐成功、改话题 respond、显式取消、缺失保存计划引用；每条两轮，加 preflight 共 9 项，要求所有项 PASS。脚本只从本地 API 调用并用 SQLite 只读连接检查 Turn/Evidence/Authority/Task/Attempt；不会写入用户消息、模型回答到 JSON。

## Windows PowerShell 运行

先在 `D:\HealthAgent` 更新代码并确认 git HEAD 与 GitHub CI 提交一致；本地尚有变更时不得覆盖。此前 API 如占用 8012，在原 API 终端使用 Ctrl+C 正常退出。保留此前实际可用的 Ollama Proxy URL/凭据配置。

**窗口 1：更新、配置、启动 API**

```powershell
Set-Location D:\HealthAgent
git status --short
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
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-e-final-head-live-v1.sqlite"
if (Test-Path $dbPath) { throw "Database already exists: use fresh filename" }
$env:DATABASE_URL = "sqlite:///" + ($dbPath -replace '\\','/')
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

**窗口 2：同一代码与数据库的只读验证**

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-e-final-head-live-v1.sqlite"
if (-not (Test-Path $dbPath)) { throw "API SQLite missing; check Window 1" }
Invoke-RestMethod http://127.0.0.1:8012/model | ConvertTo-Json -Depth 10
python -m evals.primary_wp_c_phase4_e_final_live `
  --endpoint http://127.0.0.1:8012 `
  --sqlite "$dbPath" `
  --output artifacts/wp-c/phase4-e-final-head-live-v1.json `
  --timeout 240
$exitCode = $LASTEXITCODE
Get-Content artifacts/wp-c/phase4-e-final-head-live-v1.json -Raw |
  ConvertFrom-Json | Select-Object head,verdict,summary | Format-List
if ($exitCode -ne 0) { throw "Final HEAD live Pending regression failed; retain JSON" }
```

脚本拒绝非 localhost、未建立的 SQLite、有历史 Turn 的数据库以及覆盖已有 JSON 文件。启动成功但 DB 缺失或 preflight 失败，不算真实模型验收。

## 结论规则

- 只有本地 9/9 PASS、脚本 JSON 精确 HEAD 与当前远端 HEAD 一致、最新 GitHub Actions success、已有 AC2 62/62 和 PostgreSQL 4/4，才进入最终 PR/合并条件审查。
- 单一 9-case 合同 **不能**排除模型错误将改话题误路由为 `clarify_response` 的残余风险；这属于显式的安全局限，不声称任意文本均安全。
- 原始 first-run 14/3/2 的 FAIL 历史必须保留；此前 19/19 为已知修复回归。
- 无用户显式 `AUTHORIZE MERGE #N`，**不得合并 main**。
