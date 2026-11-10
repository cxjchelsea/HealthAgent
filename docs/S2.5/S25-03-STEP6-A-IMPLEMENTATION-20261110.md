# S25-03 Step 6-A — 历史回执与证据查询集中整改（2026-11-10）

分支：`fix/s25-03-step6-a-evidence-20261110`；PR #34（Draft）；基线来自 `audit/s25-03-step6-main-20261109` 和已合并的 `main`。

## 合同与实现

1. **G03-01 P1：M4 回执来源字段丢失。** `services/plan_dispatch.py` 在仍持有可信工具 `observation` 时调用 `bounded_projection(entry.tool_name, observation)`。持久结果仅使用 `services/evidence_store.py` 既有白名单与单字段大小上限，不保存未列入字段；`recall_memory` 只保留 `found`，不得写入实际 `value`。已有 `ok`、`error` 以及 `value` 的字符串旧格式维持兼容。历史回执不能倒推缺失字段；其历史结果仍可能 unavailable。
2. **G03-03 P2：工具版本来源。** S25-03 `project_finalized_turn` 生成 `AgentAttempt` 时复制可信 `dispatch.tool_version`，不推断当前 Registry 版本，旧 NULL 保留 NULL。只读 Evidence API 增加 `tool_version`。
3. **G03-02 P2：任务历史响应。** 新增 `history_page()` 提供最新优先、最多 100 条、`has_more`。HTTP `task-history` 使用新接口；既有内部 `history()` 保持过去最早优先的正序语义（初次 CI 揭示此兼容条件，已修复）。尚未提供 cursor 访问完整旧历史，属于明确的后续能力边界。
4. **Host/权限与恢复**：不修改 Primary 或 Host 准入、M4 claim、Dispatch state machine、Step Checkpoint CAS，也不将 Trace 或模型输出当成工具回执；历史结果来自实际受信工具观察且在数据库持久化时被投影。

## 新增验证

- `tests/test_s25_03_step6_a_evidence.py`：SQLite M4 Journal→Checkpoint→S25-03 Attempt，五类工具的允许字段/非白名单过滤/记忆脱敏；历史 NULL 兼容；真实 FastAPI HTTP 的最近 100 条历史、has_more、Attempt version 与跨用户 404。
- `tests/test_s25_03_step5_postgres_physical.py`：新增 PostgreSQL 下 M4 `utc_time` 回执→Step/Dispatch→S25-03 Attempt 的物理投影/版本/非白名单过滤测试。
- **第一次 CI 发现旧内部 `history()` 正序合同回归**：1 failed、1281 passed、5 skipped；已在 `f7463c0` 恢复内部正序，HTTP 使用独立的 `history_page`，不隐瞒这一轮失败。
- 最终精确 HEAD 与 CI 结果以 PR #34 的最新 workflow 为准。在其成功前不签收 Step 6-A，不合并 PR。

## 限制与后续验收

- 非白名单字段不落库，现有 `list_tasks` 仍受原 Evidence `tasks` 白名单和大小上限约束；若涉及高敏真实待办，生产前还应定义保留期和更严格的脱敏策略，不能因“限制大小”宣称完整隐私合规。
- 历史已经丢失的 `utc/tasks/source/definition` 不做任何伪造回填。
- 此次新增自动化 HTTP 测试使用 fake Primary 作为可控路由；不等同于真实 Qwen 的跨轮执行证据验收。
- 即使最新 CI 通过，仍建议在部署前验证真实模型的 M4 非计算工具历史回放，并审查 API 的 owner 权限、分页接口需求。
- 未获用户合并授权，PR #34 保持 Draft。


## Step 6-A PR #34 二次代码复审与真实模型门禁（2026-11-10）

**审查类型：** 同一助手对 PR Git Diff 的二次代码复审，非独立人类审阅/正式 GitHub Approve。

- 已逐项核对 `services/plan_dispatch.py`、`services/s25_03_projection.py`、`services/task_state.py`、`apps/api/main.py` 变更与 SQLite HTTP、PostgreSQL 物理测试。工具调用仍由原有 M4 准入 + claim + guard + Dispatch 决定，新 `bounded_projection` 在收到观察后、持久化前运行，**不发放执行权限**。
- 与旧回执的兼容：`record_receipt` 仍记录 `ok` 和 `error`，`calculate.value` 保留字符串格式并限长，未给历史 NULL Attempt 伪造最新 `tool_version`。新增允许字段由原 `_ALLOWED_OUTPUT` 统一治理。
- 需保留的 **P2 数据保护限制**：`list_tasks` 白名单仍允许保存有界的 `tasks` 内容，和 `mcp_reference_lookup` 的检索定义。长度限制不等于完整隐私治理或防提示词污染；这些内容不得升级为指令或 Host 授权。当前属于已有输出策略的受限扩大持久化，生产部署需按个人信息类别复核保留期及最小化字段。
- **已知非目标**：HTTP `task-history` 新增最新优先和 `has_more`，但尚无 cursor 扫描更早的历史；不宣称可浏览全量事件。历史已丢失字段不得合成回填。
- 为填补真实模型证据缺口，新增 `evals/s25_03_step6_a_live.py`：强制 `RUN_S25_03_LIVE=1`、真实 provider、独立 SQLite；使用真实 Primary 先创建含 `utc_time` 首步的计划，再通过 Host 执行 FIRST，并同时核验 M4 持久 Dispatch、AgentAttempt 版本、Evidence HTTP 中的相同 `utc`。报告仅记录布尔裁决/执行元信息，不导出模型正文、记忆或 UTC 原值。
- 运行命令（Windows PowerShell 环境启动 Ollama Proxy 并设置 `MODEL_BASE_URL` 后）：
  ```powershell
  $env:MODEL_PROVIDER="ollama_proxy"
  $env:MODEL_NAME="qwen3:14b"
  $env:RUN_S25_03_LIVE="1"
  python -m evals.s25_03_step6_a_live --repeats 2 --output artifacts/s25-03-step6-a-live.json
  ```
- **门禁**：CI 检查真实模型验收脚本的安全拒绝条件，但不会自动访问用户本地 Qwen，因此 CI PASS 不等于本地真实模型 PASS。需要收到 `artifacts/s25-03-step6-a-live.json` 的实际 2/2 结果，再决定最终验收；未经合并授权不合并 PR #34。


## 真实 Qwen HTTP 第一次验收失败与契约修正（2026-11-10）

用户本机提供 `s25-03-step6-a-live.json`：`ollama_proxy/qwen3:14b`，`unified`，2 次 `plan_create` 均 `primary_v1_proposal_refused`，因此 0/2 PASS；测试没有进入 FIRST、M4 Dispatch 或 Evidence 查询阶段。**该证据不能支持“历史结果投影失败”的结论。**

追查发现原验收脚本错误要求 Primary 创建“首步 `utc_time`”的保存计划。但 `agent/primary_route_v1.py` 的 `ProposedStep.tool_name` 仅允许 `calculate` 与 `mcp_reference_lookup`，并要求每个工具参数在用户当前原文的唯一 quote 中找到。因此测试构造违反真实路由契约；原拒绝属于 fail-closed 的合理现象，**不得放宽生产 Schema 来凑测试通过**。

**整改仅涉及验收：**
- 改为“首步通过固定 MCP 参考目录查询 RAG，第二步计算 6*7”，分别使用已允许的 `mcp_reference_lookup` 与 `calculate`，实际执行的是可信本地 mock catalog，而不是任意 MCP 服务器。
- 通过真实 Primary 创建计划并执行 FIRST，不注入计划快照或伪造模型 route；对照同一 M4 Dispatch、Attempt 的 `legacy-plan-v1` 和 HTTP Evidence 中的 `definition/source`，并且不把定义原文导出报告。
- 报告增加安全诊断 `create_refusal_code` 和 `create_validation_issues`（仅枚举型 field/category，无原始提示词或私密 payload），以区分模型选错 route、quote 不匹配、Host 拒绝等情形。
- 离线回归额外验证此提示词能以合法的 `RouteSignal` 和两段精确 quote 绑定；对 `utc_time` 作为新建步骤明确断言 Schema 拒绝。
- **实际真实模型复测仍待用户运行，不得将 CI 通过等同于新脚本 2/2 PASS。**

建议用户本机重新运行：
```powershell
cd D:\HealthAgent
git fetch origin
git switch fix/s25-03-step6-a-evidence-20261110
git pull --ff-only origin fix/s25-03-step6-a-evidence-20261110
.\.venv\Scripts\Activate.ps1
$env:MODEL_PROVIDER="ollama_proxy"
$env:MODEL_NAME="qwen3:14b"
$env:RUN_S25_03_LIVE="1"
python -m evals.s25_03_step6_a_live --repeats 2 --output artifacts/s25-03-step6-a-live-v2.json
```


## 真实 Qwen v2：再次拒绝的精确诊断（2026-11-10）

用户本机 `s25-03-step6-a-live-v2.json` 仍为 `0/2`，但新 `create_refusal_code` 明确显示**两轮都是 `primary_route_invalid_step_args`**；`create_validation_issues=[]`，因而这不是 `RouteSignal` 的 Pydantic 格式错误，而是模型提出的 `plan_create` 步骤在 Host 的 `bind_route_signal()` 参数白名单检查中失败。FIRST、M4 Dispatch 和 HTTP Evidence 均未开始。**不能以此认定 S25-03 回执修复失败，也不能宣称真实模型验收通过**。

代码契约：`mcp_reference_lookup` 步骤要求参数键集合恰好为 `{"term"}`；`calculate` 必须恰好为 `{"expression"}`。检测不匹配时，Host 按既定策略 fail-closed，绝不由验收程序自动删键、补键或代替模型重提案。

本次仅修改 `evals/s25_03_step6_a_live.py`：把创建请求明确写出工具名和参数名，并通过一个**仅验收时安装**的 wrapper 调用原始 `propose_primary_route`，只输出从真实模型 JSON 计算的安全参数结构标签（`exact`、`missing_only`、`extra_only`、`missing_and_extra`、`not_object` 等）。原始模型输出直接返回原来的 Primary，未改动授权规则、未伪造或自动修补模型字段，日志不记录模型参数名称/值、提示词、个人数据。配套 `tests/test_s25_03_step6_a_live_harness.py` 检查诊断字段不会泄露参数内容。

**下次门禁**：先看 `primary_step_argument_shapes` 与 `create_refusal_code`，而不是再次盲目调宽 Schema；只有真实创建、M4 Dispatch 与 HTTP Evidence 都完成才能打 PASS。本项是模型语义提案可靠性问题，与本次 Step 6-A 持久回执修复需要分别验收。


## 真实 Qwen v3 最终 HTTP 验收（2026-11-10）

用户上传实际运行报告 `s25-03-step6-a-live-v3.json`（报告在本地，不在 PR 提交）：`real_provider=true`，`ollama_proxy/qwen3:14b`，`routing_profile=unified`，**2/2 PASS**。

- 两轮 `creation_pass`、`dispatch_pass`、`http_evidence_pass` 全为 `true`。
- 创建结果 `plan_created_pending_execution`，无拒绝码、无验证问题；两步 `mcp_reference_lookup` 和 `calculate` 的参数结构都为 `exact`。
- 首步实际执行结果 `pending_plan_steps`，M4 Dispatch 与 Attempt 的 `legacy-plan-v1` 可信版本、持久回执与 HTTP Evidence 中的 `definition/source` 均由验收脚本核对一致；第二步没有执行。
- 真实模型额外提案 `other_route` 是本次 FIRST 请求的路由诊断，不是失败或替换计划；报告不储存真实检索定义、MCP 结果正文、模型提示词或私有数据。
- 两轮耗时分别 21903ms、14238ms；仅覆盖隔离 SQLite 与可信本地 MCP mock catalog，**不能等价于生产 MCP 或 PostgreSQL 真实模型 E2E**。PostgreSQL 物理回归另由 CI 验证。
- 同一功能/代码基线 `a8f27fa` CI `1290 passed / 5 skipped`，PostgreSQL `6 passed / 2 skipped`，[Actions 42738281473](https://github.com/cxjchelsea/HealthAgent/actions/runs/42738281473) SUCCESS。

**验收裁决：S25-03 Step 6-A 中 P1 持久回执和 P2 历史窗口/版本溯源整改，在本轮约定范围内已通过自动化 + 真实 Qwen HTTP 定向验收。** 现有 `list_tasks` 内容留存策略、历史超过 100 条仅 `has_more` 而无 cursor 属于已记录的非阻断限制。此结果不是外部独立代码 Approve，也不构成用户的合并授权；PR #34 保持未合并。
