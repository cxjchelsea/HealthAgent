# WP-C Phase 4-A — 实体数据库独立回读执行手册（2026-11-04）

## 验收目标与边界

`evals/primary_wp_c_phase4_a_physical.py` 为本阶段新增的只读核验驱动，真实 Qwen 执行来自本地单独运行的 API，独立 `sqlite3` 连接通过 `mode=ro` 从**同一个全新测试数据库文件**回读，严格校验 Turn、PrimaryRouteEvidence、PendingWriteAuthority、ConversationTask、AgentAttempt。

- 场景：Task Pending 原始澄清→补标题创建→同 request_id 重放；Pending 创建→明确撤销；MCP Pending 澄清→补词查询→同 request_id 重放。
- 对每组对话验证两个真实 Turn 与两条不可变 Primary 证据一一匹配；唯一 Primary 语义尝试、快照摘要长度与 Host disposition/reason。
- Task 成功组必须有且只有一条 Task、一条成功 write Attempt、原始 Pending 授权已 `consumed` 并指向回复 Turn；撤销组必须零 Task、零 Attempt、原始授权仍 `pending` 且 `consumed_turn_id=NULL`；MCP 组必须有且仅有成功查询 Attempt、不生成 Task/写入授权。
- HTTP 重放必须返回 409，数据库不能增加 Turn、Task、Attempt 或 Primary 证据；这不是仅依赖 trace 的断言。
- 报告只含随机会话 ID、行数、状态、是否一致、HEAD，不保留任务标题、响应正文和工具参数。只有 4/4（含 preflight）才通过。

**重要**：此测试只覆盖 SQLite 单进程 API 的真实模型物理运行。PostgreSQL 双 Worker、跨进程故障恢复、prepared unknown outcome 以及取消授权最终销毁属于 Phase 4-B/C/D；不能将本脚本当成这些能力的证据。

## 准备及 PowerShell 命令

**必须停止此前 8012 端口的旧 API**。先在 `D:\HealthAgent` 的 PowerShell 更新分支，确保 working tree 不携带未审核的业务修改：

```powershell
Set-Location D:\HealthAgent
git status --short
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
git rev-parse HEAD
```

窗口 1：沿用此前通过真实模型 preflight 的 ollama_proxy / qwen3:14b / Unified / guarded / structured 环境变量（不可误接生产服务）。创建**新的**测试 DB 文件，脚本通过该文件只读回读：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$dbPath = (Join-Path (Get-Location) "artifacts\wp-c\phase4-a-physical-v1.sqlite")
if (Test-Path $dbPath) { throw "phase4-a physical database already exists; use a fresh path" }
$env:DATABASE_URL = "sqlite:///" + ($dbPath -replace '\\','/')
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

窗口 2：**传入与窗口 1 完全相同的数据库绝对路径**，不能猜测或转用此前 v3 的数据库。

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
$dbPath = Join-Path (Get-Location) "artifacts\wp-c\phase4-a-physical-v1.sqlite"
python -m evals.primary_wp_c_phase4_a_physical `
    --endpoint http://127.0.0.1:8012 `
    --sqlite "$dbPath" `
    --output artifacts/wp-c/phase4-a-physical-v1.json `
    --timeout 240
```

上传 `artifacts/wp-c/phase4-a-physical-v1.json` 以核对各组实际行数和权限生命周期。本报告先于本机物理执行生成，不可把模型输出推断为已通过。

## 自动化边界

`tests/test_primary_wp_c_phase4_a_physical.py` 用内存 SQLite 创建最小无网络证据样本，对任务成功、撤销、查询成功、额外隐蔽任务、伪造回执、重复 Turn、重复语义证据和失效授权分别做回归。此类合成验证仅证明**评分器的逻辑反例**，不是 Qwen 或生产数据的物理验收。

**Phase 4-A 状态：代码及核验规则已落库，真实模型物理报告待执行。AC4/AC5 HOLD，未经授权不得合并 main。**
