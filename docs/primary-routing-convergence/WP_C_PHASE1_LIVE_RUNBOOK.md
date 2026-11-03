# WP-C 真实 Qwen3:14b 物理验收 — 第 1 轮（Phase 1）

本轮使用**未打桩的真实 Ollama Proxy + Primary v1 + Host + 本地独立 SQLite 数据库**。运行 `evals/primary_wp_c_live.py` 只读取/创建合成会话和计划，不执行有副作用的任务或记忆写入。该脚本是第一轮冒烟，不代表 AC5 全量通过。

## 0. 更新代码（PowerShell，项目根目录）

```powershell
Set-Location D:\HealthAgent
git status --short
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

若有未提交修改，先保留/处理本地修改再切换，不要执行 `git reset --hard`。

## 1. 启动独立 API（第一个 PowerShell 终端）

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$env:DATABASE_URL = 'sqlite:///./artifacts/wp-c/live-phase1.sqlite'
$env:MODEL_PROVIDER = 'ollama_proxy'
$env:MODEL_NAME = 'qwen3:14b'
$env:UNDERSTANDING_STRONG_MODEL = 'qwen3:14b'
$env:ROUTING_PROFILE = 'unified'
$env:ROUTE_ADMISSION_MODE = 'guarded'
$env:UNDERSTANDING_STRATEGY = 'structured'
$env:M5_UNIFIED_ROUTE_ENABLED = 'true'
$env:M5_FIRST_ROUTE_ENABLED = 'false'
$env:PRIMARY_ROUTE_V1_CANARY_ENABLED = 'false'
$env:ROUTE_SHADOW_ENABLED = 'false'
$env:S25_04_ADMISSION_TRACE_ENABLED = 'false'
$env:MODEL_THINKING_ENABLED = 'false'
$env:MODEL_TIMEOUT_SECONDS = '180'
$env:MODEL_MAX_OUTPUT_TOKENS = '4096'
# 使用本机已确认可用的 Ollama Proxy URL。必要时设定：
# $env:MODEL_BASE_URL = 'http://127.0.0.1:11434/api'
python -c "from agent.config import Settings; s=Settings(); print('provider=',s.model_provider,'base=',s.model_base_url,'primary=',s.primary_route_v1_active,'route=',s.effective_routing_profile,'source=',s.routing_profile_source)"
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

保持此终端运行。**不要以 `--reload` 运行物理验收**，避免进程重启混淆 Turn 和模型请求证据。

注意：`MODEL_BASE_URL` 对 `ollama_proxy` 必须是原生 Ollama 风格 `/api` 基地址，Primary 将请求 `${MODEL_BASE_URL}/chat`，即类似 `http://127.0.0.1:11434/api/chat`。如果此前使用的是自定义代理网关，请沿用已验证的地址，不要为了示例改错。

## 2. 运行第一轮（第二个 PowerShell 终端）

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
git rev-parse HEAD
Invoke-RestMethod http://127.0.0.1:8012/healthz
Invoke-RestMethod http://127.0.0.1:8012/readyz
Invoke-RestMethod http://127.0.0.1:8012/model | ConvertTo-Json -Depth 8
python -m evals.primary_wp_c_live --endpoint http://127.0.0.1:8012 --output artifacts/wp-c/phase1-live.json --timeout 240
```

必须看到 `provider=ollama_proxy`、`routing_profile=unified`、`primary_route_v1_active=true`、`route_admission_mode=guarded`、`understanding_strategy=structured` 才允许计入验收。否则停止，纠正环境，不得解释为模型失败。

脚本依次验证：真实普通计算 (19+23)*4=168、缺失计划拒绝且无工具派发、新建两步计划仅保存不执行，并检查每轮的 durable Primary Proposal/Host evidence 与模型调用分类。三个业务场景使用**独立新会话**，失败会写入 JSON，绝不将失败静默归为 PASS。

## 3. 报告与失败处理

输出：`artifacts/wp-c/phase1-live.json`。返回码：`0` 全部通过、`1` 业务或授权断言失败、`2` 环境/传输/解码异常。

将 JSON 文件上传到对话，连同必要的服务端错误类别（**不要提供 .env、令牌、数据库真实数据或完整原始 prompts**）。后续进行 Phase 2：CREATE/FIRST/RESUME/REVISE/CANCEL；Phase 3：Pending MCP/create_task 与授权记忆；Phase 4：并发、崩溃、结果未知与重放。

**Phase 1 全部通过不代表 AC5 PASS**；未覆盖复杂操作、连续多轮和恢复语义。
