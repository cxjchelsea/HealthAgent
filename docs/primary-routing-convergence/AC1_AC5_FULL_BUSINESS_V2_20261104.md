# AC1–AC5 全业务物理验收 v2 — 冻结执行合同（2026-11-04）

## 目的、基线与非目标

此工作包仅新增 **版本化评测器** `evals/primary_ac1_ac5_business_v2.py` 及评测合同的自动化测试；不得借改变评测阈值获得虚假的业务成功。与 `main@41d91a3` 的 Primary 重构仍在 `refactor/primary-routing-convergence` 分支；本次不修改 Runtime 的任务创建、记忆、计划或提示词。

旧 `evals.primary_wp_c_phase3_live` 与 `evals.primary_wp_c_phase4_e_unseen` 的 Pending 标题续答成功合同是直接写入 `pending_task_created`。新 Runtime 的**正确行为**是 `pending_write_confirmation_required`、`awaiting_confirmation`、0 Task/Attempt，然后由结构化 `/pending-task/confirm` 消费授权，恰好 1 Task/Attempt。旧脚本及失败/成功历史报告不得重写、覆盖。

## 评测集合与物理检查

`evals/primary_ac1_ac5_business_v2.py`：22 个业务检查点 + preflight（包括实际业务成功、显式拒绝、重放、跨主链、权限约束）。

- 计划控制：CREATE A → FIRST A → RESUME A；CREATE B → REVISE B → CANCEL B。验证 route、Host disposition、预期 HTTP reason、返回步骤和本地 `conversation_plans.snapshot` 中的状态；FIRST/RESUME 只允许受保护 read-only calculate 执行。
- 普通工具：不依赖保存计划的普通计算、直接创建 task、同 request-id replay、记忆不授权的 deny、记忆授权后的 save，核对实际 `agent_attempts`、`conversation_tasks` 与测试用虚构 `user_memories`。
- Pending 写入：初始 `clarify` 授权 → `clarify_response` 仅产生候选 → 单独调用 **结构化** `POST /conversations/{cid}/pending-task/confirm` → 对同一 confirm request-id 重放 HTTP 409。候选暂存时 0 写入，最终产生 1 Task/1 Attempt。确认动作不应调用 Primary 模型，因此不能要求它拥有第二份 `PrimaryRouteEvidence`。
- Pending 撤销与改话题：各由独立会话完成；正确 `respond` 与持久 `revoked`，不存在任何 Task/Attempt。
- Pending MCP：缺词澄清后只读恢复，验证 `mcp_reference_lookup` 的实体 Attempt，任务写入为 0。
- 未保存计划引用：`plan_missing/restrict`，无工具及写入。
- 所有常规消息检验单一 `primary-route-v1` 证据、`semantic_attempts=1` 与 Primary 调用类别一次、Legacy 相关类别零调用；Replay 无新增 Turn、Attempt、权限状态；SQLite 与 HTTP 独立通道读取。

**注意：**此组合覆盖核心业务但非完整排列组合：模型/schema 伪造、跨用户认证、跨进程并发/崩溃、MCP 故障、外部工具 exactly-once 仍以此前定向自动化和专项物理证据为依据；不能以本脚本代替那些测试。CI 的 fake/stub 不等于真实 Qwen。

## 防污染与执行约束

- 本 v2 测试样本在首次真实 Qwen 运行前冻结。脚本报告包含 SHA256 case manifest 与当前 git HEAD。首次真实运行后属于已知回归集，不得将之继续称为 unseen。
- 只允许本地 `http://127.0.0.1:8012` 或 localhost 端点；必须在 **全新空白 SQLite** 上启动 API，评测器只读库，不可引用用户真实医疗记录或生产数据。
- 不允许覆盖已有报告。失败时保留原始 JSON、错误类别与当前数据库供独立分析。
- `tests/test_primary_ac1_ac5_business_v2_contract.py` 对断言计分器加负例，防止仅因为 status 200、没有副作用或新协议确认自动通过。
- 不允许用另一个额外语义分类器纠正 Primary 的 `route_kind`；单独报告语义错、准入错、物理副作用错或 provider 错。

## 本地 Windows PowerShell

先更新仓库并确认精确 HEAD，停止旧 API。一个新的命令窗口：

```powershell
Set-Location D:\HealthAgent
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
.\.venv\Scripts\Activate.ps1
$env:MODEL_PROVIDER="ollama_proxy"
$env:MODEL_NAME="qwen3:14b"
$env:ROUTING_PROFILE="unified"
$env:ROUTE_ADMISSION_MODE="guarded"
$env:UNDERSTANDING_STRATEGY="structured"
$env:PENDING_WRITE_CONFIRMATION_ENABLED="true"
# 保留此前已验证可用的 Ollama Proxy 地址及密钥环境变量
New-Item -ItemType Directory -Force artifacts\wp-f | Out-Null
$db = Join-Path (Get-Location) "artifacts\wp-f\ac1-ac5-business-v2.sqlite"
if (Test-Path $db) { throw "数据库已存在，拒绝重复使用" }
$env:DATABASE_URL="sqlite:///"+$db.Replace("\", "/")
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8012
```

在第二个 PowerShell 窗口：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
$db = Join-Path (Get-Location) "artifacts\wp-f\ac1-ac5-business-v2.sqlite"
python -m evals.primary_ac1_ac5_business_v2 `
  --endpoint http://127.0.0.1:8012 `
  --sqlite "$db" `
  --output artifacts/wp-f/ac1-ac5-business-v2.json `
  --timeout 240
$report = Get-Content artifacts/wp-f/ac1-ac5-business-v2.json -Raw -Encoding UTF8 | ConvertFrom-Json
$report | Select-Object head, verdict, summary | Format-List
$report.cases | Select-Object case, verdict, route_kind, disposition, termination_reason | Format-Table -AutoSize
```

## 验收门禁

1. 最新精确 HEAD CI 整体 success，v2 计分器定向测试通过。
2. 本机真实 `ollama_proxy / qwen3:14b` 按当前 HEAD 运行 v2；目标为 **23/23 PASS、0 FAIL、0 BLOCKED**（22 业务检查点 + preflight），不得把不确定输出改为 PASS。
3. 任一失败按模型语义、Host Admission、执行器、持久证据、运行环境分类；新增修改后需要产生新 HEAD 并重新验收，不能篡改首次运行的报告。
4. PR 创建及合并检查单独进行，只有用户明确批准合并时才能 Standard Merge Commit。当前 `Phase 4-E=HOLD`。
