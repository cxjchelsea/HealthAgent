# S25-02 Final — Step 5 默认主链集中验收执行规程

> Status: **LOCAL REAL QWEN + PHYSICAL EVIDENCE PENDING**. Do not mark Step 5 PASS using GitHub CI alone.
> Working branch: `refactor/s25-02-final-mainline-convergence`. No PR / no main mutation until acceptance.
> The prior M5 11/11 and SQLite 34/34 belonged to the previous explicitly enabled profile and **do not** count as post-default acceptance.

## Goal and evidence gates

1. Exact branch HEAD and working-tree state recorded, API restarted with fresh isolated SQLite, `ollama_proxy / qwen3:14b`.
2. **No routing overrides** in API *or* test shell, including legacy flags and `ROUTING_PROFILE`, nor in `.env`. The API must return `routing_profile=unified`, `routing_profile_requested=auto`, **`routing_profile_source=auto_real_provider_default`**, `understanding_strategy=structured`, `route_admission_mode=guarded`, `m5_unified_route_enabled=true`.
3. Same existing 11 real-model scenarios: 11 PASS / 0 FAIL / 0 BLOCKED; primary=1, plan_intent=0 where applicable; replay=409, no extra dispatch.
4. Independent same-isolated-DB physical readback: all checks PASS, SQLite integrity PASS, exact two checkpointed dispatches.
5. Explicit legacy rollback/invalid mixed config tests on exact code HEAD all CI-success. Capture source/argument/scope/authorization negatives and PG recovery CI evidence; describe PostgreSQL real-Qwen E2E as NOT COVERED unless performed separately.

## Terminal A — clean working branch, fresh server

Start in `D:\HealthAgent` with your existing Python virtual environment and Ollama running. Stop previous API on port 8012 first.

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
git fetch origin
git switch refactor/s25-02-final-mainline-convergence
git pull --ff-only origin refactor/s25-02-final-mainline-convergence
git status --short
git rev-parse HEAD

$dir = "artifacts\s25-02-final-step5-default"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if (Test-Path "$dir\healthagent.db") {
  throw "Existing Step 5 SQLite DB present. Choose a new isolated acceptance directory."
}
foreach ($name in @(
  "ROUTING_PROFILE", "ROUTE_ADMISSION_MODE", "UNDERSTANDING_STRATEGY",
  "M5_UNIFIED_ROUTE_ENABLED", "M5_FIRST_ROUTE_ENABLED", "ROUTE_SHADOW_ENABLED"
)) {
  [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
$env:DATABASE_URL = "sqlite:///./artifacts/s25-02-final-step5-default/healthagent.db"
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

**Do not** place incompatible historical routing variables in `.env`. Env removal in the shell does not override any `.env` entries. The server's `routing_profile_source` provides a check.

## Terminal B — model profile probe, 11 cases, independent readback

In a separate PowerShell window:

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
foreach ($name in @(
  "ROUTING_PROFILE", "ROUTE_ADMISSION_MODE", "UNDERSTANDING_STRATEGY",
  "M5_UNIFIED_ROUTE_ENABLED", "M5_FIRST_ROUTE_ENABLED", "ROUTE_SHADOW_ENABLED"
)) {
  [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
$env:RUN_S25_02_M5_UNIFIED_LIVE = "1"

$dir = "artifacts\s25-02-final-step5-default"
$head = git rev-parse HEAD
$head | Out-File "$dir\git-head.txt" -Encoding utf8
$profile = Invoke-RestMethod "http://127.0.0.1:8012/model"
$profile | ConvertTo-Json -Depth 10 |
    Out-File "$dir\runtime-profile.json" -Encoding utf8
$profile | Format-List
if (
    $profile.provider -eq "fake" -or
    $profile.routing_profile -ne "unified" -or
    $profile.routing_profile_requested -ne "auto" -or
    $profile.routing_profile_source -ne "auto_real_provider_default" -or
    $profile.route_admission_mode -ne "guarded" -or
    $profile.understanding_strategy -ne "structured" -or
    $profile.m5_unified_route_enabled -ne $true
) {
    throw "Default-profile attestation FAIL. Stop, inspect API environment and .env."
}

python -m evals.s25_02_m5_unified_live `
  --endpoint http://127.0.0.1:8012 `
  --require-default-profile `
  --output "$dir\live.json" `
  --timeout 240
$liveExit = $LASTEXITCODE
if ($liveExit -ne 0) { throw "11-case LIVE acceptance failed. Do not run physical acceptance." }

python -m evals.s25_02_m5_unified_physical `
  --database "$dir\healthagent.db" `
  --live-report "$dir\live.json" `
  --output "$dir\physical.json"
$physicalExit = $LASTEXITCODE
if ($physicalExit -ne 0) { throw "SQLite physical readback failed." }

$live = Get-Content "$dir\live.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$physical = Get-Content "$dir\physical.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$live.records | Format-Table case,verdict,http,model_http_attempts -AutoSize
$live.summary
$physical.summary
```

**Attention:** both terminals must be in the same working directory, using exactly the same isolated SQLite file; do not reuse old M5 DB or reports. `git-head.txt` attests the local worktree only; compare with the server startup provenance/active process and GitHub exact HEAD. Local reports are developer-supplied evidence, not independent remote physical access.

## What to send for independent review

Upload these three files (and `git-head.txt` if available):
- `artifacts\s25-02-final-step5-default\live.json`
- `artifacts\s25-02-final-step5-default\physical.json`
- `artifacts\s25-02-final-step5-default\runtime-profile.json`

Do **not** upload the SQLite DB unless the acceptance review specifically requires a second direct forensic read; it may contain messages or sensitive data.

A real Qwen failure, default-profile mismatch, failed physical check or unavailable CI means Step 5 is **NOT PASS**. Repair a proven defect on the SAME branch and rerun a **fresh isolated** matrix, then assess Step 6.

## Known limitations

- This acceptance checks the supported 11 lifecycle/ordinary/pending scenarios and SQLite durable execution evidence; does not certify all ambiguous-language variants, production authentication, external exactly-once, or real-Qwen PostgreSQL E2E.
- The current generic AgentLoop goal understanding may use additional model calls; this is **not** automatically another primary plan intent decision. Use `primary` and `plan_intent` category counts, not raw total HTTP attempts, for route ownership.
- Legacy mode is available as an explicit rollback; do not call a rollback test a default unified test.
