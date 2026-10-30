# S25-02 Final — Step 6 Shadow 真实 Qwen3 定向复验

状态：**脚本就绪，真实模型结果待本地执行。** 测试修复范围只有 Shadow 诊断；不得把自动化 CI 通过误记为真实 Qwen3 修复通过。

## 前置约束

- 本地 D:\HealthAgent 使用 PR #27 当前分支 `refactor/s25-02-final-mainline-convergence`，Terminal A / B 均执行 `git rev-parse HEAD` 并记录。
- 两个 Terminal 均进入同一个目录；`.env` 中历史 `UNDERSTANDING_STRATEGY=structured` 等路由覆盖继续注释。
- 模型运行 Ollama Qwen3:14b，端口 `8012` 的旧 API 必须先 Ctrl+C 停止。**使用全新独立 SQLite 文件，不复用此前 11+34 报告数据库。**
- Terminal A 仅为本次诊断显式设置 `ROUTE_SHADOW_ENABLED=true`；它不是四项 M5 核心路由覆盖参数，不会改变真实模型 `auto_real_provider_default`。
- Shadow 为 POST-COMMIT 诊断，运行本次测试不会使其获得工具执行权限。
- 定向验收只覆盖 3 个场景，不替代先前 11+34，也不证明真实 Qwen PostgreSQL E2E。

## Terminal A — 重启隔离服务

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
git fetch origin
git switch refactor/s25-02-final-mainline-convergence
git pull --ff-only origin refactor/s25-02-final-mainline-convergence
git status --short
git rev-parse HEAD

$dir = "artifacts\s25-02-final-shadow-rerun"
New-Item -ItemType Directory -Path $dir -Force | Out-Null
if (Test-Path "$dir\healthagent.db") { throw "请换一个全新隔离目录；不能复用旧 SQLite" }

foreach ($name in @("ROUTING_PROFILE","ROUTE_ADMISSION_MODE",
  "UNDERSTANDING_STRATEGY","M5_UNIFIED_ROUTE_ENABLED","M5_FIRST_ROUTE_ENABLED")) {
  [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
$env:ROUTE_SHADOW_ENABLED = "true"
$env:DATABASE_URL = "sqlite:///./artifacts/s25-02-final-shadow-rerun/healthagent.db"
$env:MODEL_PROVIDER = "ollama_proxy"
$env:MODEL_BASE_URL = "http://127.0.0.1:11434/api"
$env:MODEL_NAME = "qwen3:14b"
$env:UNDERSTANDING_LIGHT_MODEL = "qwen3:14b"
$env:UNDERSTANDING_STRONG_MODEL = "qwen3:14b"
$env:MODEL_TIMEOUT_SECONDS = "180"
$env:MODEL_MAX_OUTPUT_TOKENS = "2048"
$env:ALLOW_DEMO_IDENTITY = "true"
$env:USER_TOKENS = ""
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

## Terminal B — 仅三项诊断复验

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
foreach ($name in @("ROUTING_PROFILE","ROUTE_ADMISSION_MODE",
  "UNDERSTANDING_STRATEGY","M5_UNIFIED_ROUTE_ENABLED","M5_FIRST_ROUTE_ENABLED")) {
  [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
$env:RUN_S25_02_M5_UNIFIED_LIVE = "1"
$dir = "artifacts\s25-02-final-shadow-rerun"
New-Item -ItemType Directory -Path $dir -Force | Out-Null
git rev-parse HEAD | Out-File "$dir\git-head.txt" -Encoding utf8
$profile = Invoke-RestMethod "http://127.0.0.1:8012/model"
$profile | ConvertTo-Json -Depth 10 |
  Out-File "$dir\runtime-profile.json" -Encoding utf8
$profile | Format-List
if ($profile.routing_profile_source -ne "auto_real_provider_default" -or
    $profile.routing_profile -ne "unified" -or
    $profile.route_shadow_enabled -ne $true) {
  throw "API 的 Unified 默认模式或 Shadow 诊断未开启"
}
python -m evals.s25_02_m5_unified_live `
  --endpoint http://127.0.0.1:8012 `
  --require-default-profile `
  --shadow-regression-only `
  --output "$dir\shadow-live.json" `
  --timeout 240
if ($LASTEXITCODE -ne 0) { throw "Shadow 定向 LIVE 复验失败" }
$report = Get-Content "$dir\shadow-live.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$report.records | Format-Table case,verdict,http,shadow_state,shadow_route,shadow_retry_calls
$report.summary
```

## 判定

1. `runtime-profile.json` 中 `route_shadow_enabled=true`、`routing_profile_source=auto_real_provider_default`。
2. `shadow-live.json` 3 PASS / 0 FAIL / 0 BLOCKED，REVISE 的 Shadow `route=plan_revise`、PENDING 的 Shadow `route=clarify`，两者 `state=valid`。
3. Shadow 的 `execution_authorized` 和 `host_admission_complete` 必须为 false。
4. 脚本要求原有主链语义 `primary=1`、`plan_intent=0`。
5. 即使第一轮仍出现 `unavailable`，也不得伪造通过；按原始报告中的 bounded validation_code 分析，必要时只修复真正缺陷。
6. 此次仅定向测试 Shadow，**不运行原 SQLite 34 项校验器**，因为其依赖完整 11 场景记录，不能对 3 场景报告使用。

请上传 `shadow-live.json`、`runtime-profile.json`、`git-head.txt` 进行独立复审。主分支仍保持不变，PR #27 不得在用户授权前合并。
