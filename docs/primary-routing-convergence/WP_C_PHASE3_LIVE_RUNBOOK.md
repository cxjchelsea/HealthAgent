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


## Phase 3 首轮真实模型证据与整改（2026-11-03）

本机用户报告 `phase3-live-v1.json`，精确代码 HEAD `b19244012bc4324e375fe5ba3db9d47074a73002`，ollama_proxy/qwen3:14b。**8 PASS、3 FAIL、3 BLOCKED**。未知计划、普通任务和重放、记忆允许/拒绝均通过；失败项：

- `ORDINARY_WITH_PLAN`：已正确绑定 `ordinary_tool/allow`，成功调用 calculate，保存计划仍是 pending/pending，但最终回显 `status=partial, termination_reason=pending_plan_steps`。根因是普通 AgentLoop 把独立请求与未完成计划的生命周期混同。
- `PENDING_TASK_ANSWER`：回复被 Qwen 再次判为 `clarify`，最终 `needs_input`；未执行任务。模型提案层没有准确地区分“验证后的 Pending 回复”与新的澄清请求。
- `PENDING_MCP_ORIGIN`：`clarify` 返回 `primary_v1_clarification_required`，未持久化后续 Pending 来源。原 Primary Schema 仅承载 pending_write_tool_name，缺少明确的只读 MCP Pending 字段。

整改：`agent/loop.py` 为独立 read_only ordinary 设置明确隔离，不让普通工具 observation 完成保存计划的步骤或把其 pending 判作本轮失败；`agent/primary_route_v1.py` 增加仅 clarify 可用的 `pending_read_tool_name=mcp_reference_lookup` 提案字段，并约束 verified pending reply 为 `clarify_response`；`agent/primary_route_v1_canary.py` 对该提案建立 `needs_input:mcp_reference_lookup` Pending 事实，但不预先执行任何工具。增加 AgentLoop 和 HTTP Pending 测试。

以上为**针对原始失败的实现修复**，没有改动 `evals/primary_wp_c_phase3_live.py` 的业务通过条件。尚未用 Qwen 对整改版本执行完整物理复验，Phase 3 仍 HOLD。

定向复验可使用：
```powershell
python -m evals.primary_wp_c_phase3_live --endpoint http://127.0.0.1:8012 --groups plan_ordinary pending_task pending_mcp --output artifacts/wp-c/phase3-targeted-v2.json --timeout 240
```


## Phase 3 第二轮定向验收与整改（2026-11-04）

本机真实 Qwen3:14b 证据为用户上传的 `phase3-targeted-v2.json`，测试代码 HEAD `75d519bd8a9555bc9ae3610ff4fad012b8a6576e`，ollama_proxy、unified。运行 `--groups plan_ordinary pending_task pending_mcp`。结果：**5 PASS、2 FAIL、2 BLOCKED**（包含 preflight）。

- PLAN_SETUP 与 ORDINARY_WITH_PLAN 均 PASS：独立 ordinary 工具返回 completed/model_final，calculate 成功，计划两步仍 pending，验证了 AgentLoop 隔离整改。
- PENDING_TASK_ORIGIN PASS；PENDING_TASK_ANSWER FAIL：HTTP 200，Primary route_kind=clarify/Host=clarify，reason=needs_input，未执行 create_task；重放 BLOCKED。任务续答尚未正确路由为 clarify_response，不能把澄清当成功。
- PENDING_MCP_ORIGIN PASS：成功形成 needs_input；PENDING_MCP_ANSWER FAIL：Primary 拒绝，`primary_route_invalid_signal`，validation_issues 仅 `root/other`。上一代码版本未列出新加入的 `primary_route_pending_read_invalid` 错误码，故无法直接断定具体字段。重放 BLOCKED。

本轮修复严格限定在单一 Primary 路由的**可信 Pending 上下文与输出合同**：
1. 把经过 Host 检查的 `pending_origin_verified` 布尔值和 `pending_question`（最多 500 字）与 `pending_action` 一并传给唯一 Primary 模型；不带未经认证的 Pending 上下文。
2. 明确非取消、非变更主题且已补齐缺失参数的回复使用 `clarify_response`；其全部 optional 字段（包括 `pending_read_tool_name`、`pending_write_tool_name`）必须为 null。加入完整可被 Schema 校验的 Pending 输出结构示例。
3. 诊断白名单列出 `primary_route_pending_read_invalid` 并标记 `pending_read_tool_name`，但仍不输出原始模型参数、消息、提示词或敏感内容；方便区分实际错误而非盲目降低 Schema。
4. 单元回归检查一次模型调用、认证和未认证上下文的隔离、无权限放大、合法 Pending JSON 示例。

**此版本还没有进行新一轮真实 Qwen 物理验收**，既有 2 FAIL 不能标成通过。Pending 续执行仍由持久原始授权与 Host 负责，既不新增语义模型裁决，也不因 Schema 失败而回退 Legacy。原始测试挑战已被用于定位，下一阶段需要另建陌生测试集验证泛化。

定向本机复验：
```powershell
python -m evals.primary_wp_c_phase3_live --endpoint http://127.0.0.1:8012 --groups pending_task pending_mcp --output artifacts/wp-c/phase3-pending-v3.json --timeout 240
```

只有通过真实复验再进入 Phase 3 完整非回归；AC5 仍 HOLD，禁止未经用户明确授权合并。


## Phase 3 Pending 定向 v3 结果及结构化生成修正（2026-11-04）

用户真实模型报告 `phase3-pending-v3.json`，代码 HEAD `d101b040a72ff400e0e49308ccbf035dd1bbe3a4`，ollama_proxy/qwen3:14b，unified，**2 PASS、2 FAIL、3 BLOCKED**：

- preflight PASS；PENDING_MCP_ORIGIN PASS，正确落为 `needs_input`。
- PENDING_TASK_ORIGIN FAIL：`primary_route_quote_not_found`，一次 Primary 调用、0 工具；任务续答与重放被阻断。
- PENDING_MCP_ANSWER FAIL：`primary_route_invalid_signal`，现在诊断能定位为 `primary_route_pending_read_invalid`，模型在答复已验证 Pending 时仍携带不允许的 Pending 读取意图字段；0 工具、无旧分类器回退。重放 BLOCKED。

两次 Prompt 细化后，失败位置改变但没有完全消失，因此优先从**单次模型的结构化生成边界**下手，而不是增加无尽示例或跳过 Host。新增 `primary_route_generation_schema`：对于长度 1–400 的当前请求，约束生成的根 `quote` 必须是该请求本身（准确的唯一 Host 来源片段）；对于 Host 验证后的 Pending 回复，单模型生成 Schema 要求两个 `pending_*_tool_name` 字段为 null。未能满足时依然严格拒绝；代码不删除/重写模型输出字段、不替用户决定意图、不强制选择 `clarify_response`。原 RouteSignal 和 Host 授权规则仍独立验证。对较长请求保持原始可选 quote 方式。

**注意边界**：完整当前消息可作为来源，但不代表模型的 `route_kind` 已经得到独立的语义正确性证明。生成约束仅防止不忠实的引用或复用上一轮 pending 标记；真正的路由质量仍需未调参挑战样本与真实 Qwen 物理回归。

本次修正不得直接宣称已解决待办续答：即使生成字段正确，模型依然可能选择 `clarify` 而非 `clarify_response`，只能真实测量后处理。仍需保留 409 重放及 0 工具的负断言。

定向真实运行命令：
```powershell
python -m evals.primary_wp_c_phase3_live --endpoint http://127.0.0.1:8012 --groups pending_task pending_mcp --output artifacts/wp-c/phase3-pending-v4.json --timeout 240
```


## Phase 3 Pending v4 结果及任务写入合同修复（2026-11-04）

真实 Qwen3:14b 报告 `phase3-pending-v4.json`，精确测试 HEAD `1259a5fd7dcff6dc3fdd6f89ba9fea5ac4328e79`。**5 PASS、1 FAIL、1 BLOCKED**（含预检）：Task 首次澄清成功；Task 回复 `primary_route_invalid_signal`，具体 `primary_route_unexpected_write_contract`，未执行工具；其 REPLAY BLOCKED。MCP 首次澄清、补参后 `clarify_response` 查询及 REPLAY HTTP 409 全部 PASS，0 次重放模型/工具调用。

本轮只补齐已认证 Pending 回复中**重新生成新写入合同**的缺口。在 `primary_route_generation_schema` 的 `pending_origin_verified=true` 路径下，除了 `pending_read_tool_name`、`pending_write_tool_name`，模型生成 `write_tool_name` 和 `write_arguments` 也必须为 JSON null。该约束只影响带有 Host 验证 Pending 来源的模型输入：普通当前轮明确要求新建任务/保存记忆的生成 Schema 不变。唯一 Primary 模型仍选择路由，Host 仍读取原始待办授权并从当前用户回复验证标题；如果模型违反约束，`RouteSignal` 仍拒绝，不修补、不重试、不转给 Legacy。

添加生成 Schema 边界、单次模型调用、正确续答及恶意/错误字段 fail-closed 的回归。**该整改尚无真实模型通过证明**；v4 的单次合法 MCP PASS 不代表 Pending Task 已通过，Phase 3 继续 HOLD。

定向新数据库复验：
```powershell
python -m evals.primary_wp_c_phase3_live --endpoint http://127.0.0.1:8012 --groups pending_task pending_mcp --output artifacts/wp-c/phase3-pending-v5.json --timeout 240
```


## Phase 3 Pending v5：真实 Qwen 定向物理验收 PASS（2026-11-04）

依据用户在本机生成并上传的 `phase3-pending-v5.json`；精确执行代码 HEAD：`f04e10d2d7d42aa8d9d0102b7ce82e9f0428824b`。运行环境 ollama_proxy / qwen3:14b / unified / Primary v1。**7 PASS、0 FAIL、0 BLOCKED**。

| 场景 | 验证结论 |
| --- | --- |
| preflight | PASS，真实模型与 Primary v1 |
| PENDING_TASK_ORIGIN | PASS，clarify / needs_input，0 工具调用 |
| PENDING_TASK_ANSWER | PASS，clarify_response / Host allow，admission_reason=primary_v1_pending_task_authorized；完成一次 create_task，termination_reason=pending_task_created |
| PENDING_TASK_REPLAY | PASS，HTTP 409，0 模型和工具调用 |
| PENDING_MCP_ORIGIN | PASS，clarify / needs_input，0 工具调用 |
| PENDING_MCP_ANSWER | PASS，clarify_response / Host allow，admission_reason=primary_v1_pending_read_only；一次成功 mcp_reference_lookup，termination_reason=verified_clarification_execution |
| PENDING_MCP_REPLAY | PASS，HTTP 409，0 模型和工具调用 |

各正常业务请求的 `categories.primary=1`、`semantic_attempts=1`，没有把总 HTTP 模型调用数等同于语义调用数。真实物理结果证实定向 Pending 任务与只读 MCP 补参链路可工作；本报告不包含独立数据库原始回读或本机原始 JSON 入库，不能代替 Phase 4 持久性验证。

同一精确 HEAD 的 GitHub Actions `41751171449` 已通过，1107 passed / 3 skipped（这是 CI，不是 Qwen 物理运行）。

**当前裁决：Pending 定向验收 PASS；完整 Phase 3 非回归仍 NOT RUN、总体 HOLD。** 下一步在新独立数据库和同一代码 HEAD 上运行不带 `--groups` 的完整 `evals.primary_wp_c_phase3_live`，全部 14 项（preflight + 13 请求）必须 PASS、0 FAIL、0 BLOCKED，然后才能讨论 Phase 3 收口与未用于整改的挑战语句；Phase 4 与 AC5 仍 HOLD。
