# WP-C Phase 3 — 真实模型异常、权限及 Pending 验收（准备与执行）

## 准入与作用域

Phase 1 真实 Qwen3:14b HTTP 4/4 PASS；Phase 2 最终完整 9/9 PASS，对应精确代码 HEAD `dcfa3878313ef15507a0c6e7fd42b561fd5a90af`。Phase 2 归档：`WP_C_PHASE2_FINAL_ACCEPTANCE_20261103.md`。**Phase 3 尚未做真实模型物理验收，不能宣称 PASS。**

使用 `evals/primary_wp_c_phase3_live.py`。测试要求真实 Ollama Proxy / qwen3:14b、Unified、guarded、structured、Primary v1。**只准使用独立、可丢弃的测试数据库，不能连接生产/共享数据库**：包含真实的本地任务写入和合成记忆键值；MCP 只查询公开术语 RAG，不发送个人资料。

脚本不修改 Prompt，不替模型重分类，不将任意拒绝算作通过。失败记录固定类别和 Host 的 durable evidence，不记录可识别的记忆值、回答原文或模型原始输出。各组新建独立会话；某组前一步失败，后续该组 BLOCKED。

## 测试矩阵（13 请求 + preflight）

| 分组 | 请求 | 不变量 |
| --- | --- | --- |
| plan_ordinary | PLAN_SETUP → ORDINARY_WITH_PLAN | 有待执行计划的情况下，独立计算 `(27-9)*3` 仍必须执行并返回 54；不能执行或修改保存计划、不能自动拒绝 |
| unknown | MISSING_REFERENCE | 引用不存在的旧计划，Host restrict 且不得派发 |
| task | TASK_WRITE → TASK_REPLAY | 只有原始已授权本地任务写入一次；重放 HTTP 409、0 模型及工具 |
| memory_denied | MEMORY_DENY | 未提供 `memory_write_consent`，应被 Host deny、0 写入；不能以通用拒绝冒充权限判定 |
| memory_allowed | MEMORY_ALLOW | 明确当轮 consent=true，合成 key/value 成功保存，Host allow，1 次成功 save_memory；本阶段不证明跨用户记忆隔离 |
| pending_task | PENDING_TASK_ORIGIN → PENDING_TASK_ANSWER → PENDING_TASK_REPLAY | 缺标题先澄清，之后经 durable 原始授权完成单次 create_task，重放拒绝 |
| pending_mcp | PENDING_MCP_ORIGIN → PENDING_MCP_ANSWER → PENDING_MCP_REPLAY | 缺术语先澄清，答复后按来源查 MCP，必须有 `clarification_resumed` 成功回执；不允许旧分类器补判 |

期望每轮合法 Primary 仅 1 次模型语义路由，13 项 Legacy 主路由分类器无额外调用（报告使用模型调用类别抽样；此前自动化有函数级 fail-on-call 证据）。后续 Phase 4 仍须做独立物理数据库回读、并发、进程崩溃、未知结果重放。

**特别说明：** 静态检查发现 `agent/primary_route_v1_canary.py` 的普通 clarify 分支只返回 `primary_v1_clarification_required`，不显式创建 `needs_input:mcp_reference_lookup` 的 pending 状态；是否能够经同一真实 HTTP 请求建立 Pending MCP 需实测核对。如失败必须独立定位补齐，不能调低验收断言。其他失败同样不得写进 Prompt 后直接认作新挑战泛化。

## Windows 执行

停止旧的 8012 API 后，更新并核对分支：

```powershell
Set-Location D:\HealthAgent
git status --short
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
git rev-parse HEAD
```

在**启动 API 的 PowerShell** 中保留此前已验证的 Ollama Proxy/Qwen3:14b、Unified、guarded 和 structured 环境变量。重启前指定独立数据库：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
$env:DATABASE_URL = 'sqlite:///./artifacts/wp-c/phase3-isolated.sqlite'
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

如历史 DB 已存在，使用新的独立文件名，切勿复用生产库。模型 API 的 `/model` 预检不会证明 DB 隔离，隔离必须由启动者确认。

另一个 PowerShell：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
Invoke-RestMethod http://127.0.0.1:8012/model | ConvertTo-Json -Depth 8
python -m evals.primary_wp_c_phase3_live `
    --endpoint http://127.0.0.1:8012 `
    --output artifacts/wp-c/phase3-live-v1.json `
    --timeout 240
```

若只复测部分独立组，可传入 `--groups pending_mcp pending_task` 或 `--groups plan_ordinary unknown`。该命令会创建新会话。将 `phase3-live-v1.json` 上传审核；最终必须全部组 PASS 才能宣布 Phase 3 完成。

## 合并门禁

WP-C Phase 1/2 PASS；Phase 3 NOT RUN；Phase 4 NOT RUN；AC1–AC5 最终复核 HOLD；不合并 `main`，除非用户明确授权标准合并。
