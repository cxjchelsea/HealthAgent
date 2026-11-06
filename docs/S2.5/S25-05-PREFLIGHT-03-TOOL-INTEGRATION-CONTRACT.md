# S25-05 前置收口 03 — Tool Registry 与现有主链的接入合同

**阶段：实施前架构边界冻结；不是 S25-05 Step 4 实现授权。**
权威代码基线：`main@cdde8ebb6c278dda752a744f75051159285ff65b`，2026-11-06。
参考旧方案：分支 `docs/s25-05-capability-tool-governance-design-20261022` 的 `docs/S2.5/S25-05-能力扩展与工具治理-前三步设计.md`，**旧方案没有合并到 main，且仍待按新主链重审**。

## 1. 不变式（强制）

1. **Primary（语义所有权）**：在真实模型 Unified 下，一轮用户请求由 `agent/primary_route_v1.py` 提出唯一主语义路由；不能由 Registry、S25-04 选择器、Tool Adapter 或 MCP 远端返回值新增第二个顶层意图改判器。方案内的 Tool Calling 是 Host 许可范围内的子决策。
2. **Host（授权所有权）**：`agent/primary_host_admission_v1.py` 根据可信 Turn、原始用户输入、Checkpoint、Pending 来源、写入同意和受限 Scope 作确定性准入。工具描述、模型选择、MCP `tools/list` 和 Registry 启用状态 **均不能扩大 Host 批准的权限**。不能把工具“可发现/已曝光”当作“获授权”。
3. **AgentLoop（受限执行循环）**：保持正式入口 `POST /conversations/{cid}/agent/messages` 和原 LangGraph/AgentLoop。在 Host 指定的执行模式和工具允许集合内部调用注册工具；Observation 可以驱动局部下一步，但不能改写已经批准的业务 Route 或制造新写入许可。
4. **Tool Registry（可信能力目录）**：提供版本化 Tool Definition、Pydantic Schema、只读/写入标记、Adapter 绑定、可用性、稳定错误码和 Provider 兼容曝光视图。Registry 自身不读取模型提示决定用户授权，不产生新的业务计划，也不负责长短期记忆意图理解。
5. **Tool Invoker（最终执行检查）**：每次调用同时核验工具已注册、仍启用、此 Turn 曾被合法曝光、允许的版本、参数与可信 Schema 一致、Host 许可和工具特定权限；任一不满足则 fail-closed。调用只发生在这些检查之后。
6. **MCP（不可信外部能力描述）**：只允许在可信服务清单内发现远端工具，`tools/list` 最多缩小/验证本地已批准 Tool Definition，不得自动提升权限；远端返回文本是数据不是系统指令，不能凭远端描述注册可执行危险工具。
7. **Evidence（执行审计）**：不削弱 `PrimaryRouteEvidence`、`AgentAttempt`、`PlanLedger`、`PlanDispatchJournal`、`PendingWriteAuthority` 的既有约束；已执行或结果未知的写入不得自动重试。工具名称、版本/来源、参数摘要、错误分类和收敛的 Observation 需要可追查，且不能泄露明文敏感参数。

## 2. 真实代码中已经存在的边界（不可覆盖）

| 当前入口 | 已实现机制 | S25-05 的接线要求 |
| --- | --- | --- |
| `apps/api/main.py` Primary/Host | Primary 结果落 `PrimaryRouteEvidence`、Host 判定，再路由至已有 Executor | 不改为 Registry 先执行后“补授权”，也不在注册层直接返回业务终态 |
| `agent/primary_host_admission_v1.py` | 普通只读、有限写入 `create_task/save_memory`、保存计划、Verified Pending 的确定性准入 | 新写入 Tool **默认不能**经 `ordinary_tool` 动态扩权；需要独立权限设计及测试之后才能放行 |
| `agent/loop.py` | `TOOL_SCHEMAS` 静态曝光；`read_only` 与 `tool_allowlist` 再收窄，现有 `calculate/utc_time/list_tasks/recall_memory/mcp_reference_lookup` 名称白名单 | **不得移除旧 read-only fence 后仅靠 Registry 的 `effect=read` 判断**；分阶段平行核验，单个可证明只读新工具逐个显式准入 |
| `agent/tools.py` | `SCHEMAS` / `TOOL_SCHEMAS` / `execute_tool` 分散 | S25-05 才实现注册/Schema/Adapter 单一可信来源，先做演示只读工具的最小纵向切片 |
| `services/primary_pending_confirmation.py` | Pending `create_task` 标题暂存、Host 结构化确认、原始来源绑定、nonce/digest/TTL、事务消费 | 新目录 **不能**将此链路转为文本“Yes”或模型自行授权，也不能在通用 Invoker 中直接执行 Pending 写入 |
| `agent/primary_pending_readonly_v1.py` | MCP Pending 有来源验证与已准备/已完成 Attempt；不自动重试未知结果 | 先保留 `needs_input:mcp_reference_lookup` 与专用语义提示，待 S25-05 验证可泛化的 Pending 合同后才能讨论抽象 |
| Legacy/Fake 路由 | 旧审查函数仍在 `send_agent_in_conversation` 后半段；Fake fixture 和显式 Legacy 仍会使用 | 接口必须显式兼容或者拒绝；Legacy 不得成为新增 Registry 的第二主语义审查路径 |

## 3. 接口与数据合同（待设计 Step 1–3 重新审查和冻结）

设计概念：
`ToolDefinition(name, version, input_schema, effect, adapter_key, enabled, capability_ids, auth_policy_id)`、
`ToolRegistrySnapshot`、`ToolExposure`、`ToolInvocation`、`ToolObservation`。

- `Catalog`：只提供已审核 Tool 的描述/版本/能力索引，不含 token、启动命令、Python 可执行路径等机密。元数据不等于执行许可。
- `Exposure`：由系统信任的目录快照与 Host 已批准执行范围、运行时环境可用性求交集；动态语义 TopK 只能进一步收窄。
- `Invocation`：在派发前重新核验 registry snapshot、实时启用态、工具版本、Pydantic 参数、Host policy 与 per-turn 许可。不能把普通只读的允许集合自动扩大成新写入工具。
- `Adapter`：固定受信本地实现/明确批准的 MCP 服务映射；不可直接执行模型传入的 Python 路径、命令、URL，不能为模型输出构造提权参数。
- `Observation`：保持现有 `ok` 兼容，并增加必要的 `tool_name/tool_version/error_code/source_ref` 等有限字段；原始敏感参数和不可信远端指令只作为数据处理。
- **恢复与版本漂移**：PlanLedger 既存 `expected_tool`、`expected_arguments` 的绑定不可静默改成新工具；不兼容版本必须阻断，未知副作用进入核对流程。

## 4. 安全负例清单（S25-05 Step 5 应覆盖）

| 编号 | 触发条件 | 必须观察的结果 |
| --- | --- | --- |
| G1 | 模型要求调用已注册但未曝光工具 | `tool_not_exposed`，适配器未运行 |
| G2 | 模型指定未注册、已停用或版本冲突的工具 | 拒绝，且不以另一个工具静默替代 |
| G3 | Pydantic Schema 不符或参数额外字段 | 拒绝，未发生副作用 |
| G4 | 标记写入的工具被模型放入普通只读执行 | Host/Invoker 双层拒绝 |
| G5 | MCP 返回诱导执行其他工具/系统命令的文本 | 作为不可信 Observation，不改变路由和权限 |
| G6 | Pending `create_task` 没有结构化确认令牌 | 无 Task/Attempt 写入，维持已验收边界 |
| G7 | 工具失效、超时、断线、Provider 错误 | 稳定错误类别，不伪造成功，不自动重放未知写入 |
| G8 | Checkpoint 绑定旧工具版本 | 版本不匹配时阻断/人工核对，不静默替换语义 |
| G9 | 真实 Qwen 使用新增只读演示工具 | 能通过正式 API 到达同一授权栅栏，正确 Observation，真实失败率按样本如实记录 |
| G10 | Fake/Legacy 与 Unified 双模式 | 不新增第二个 Primary，显式旧兼容路径不绕过原授权 |

## 5. 05 的首个实现切片（仅作为建议，不在本次实现）

**首先实现可信只读演示工具（例如原 Step 1–3 中的 `text_stats`）**，验证 Registry 定义 → Provider Schema → AgentLoop 选择 → Host 允许范围 → Adapter → Attempt/Observation/Trace。

但 `text_stats` **不会**因为 Registry 注册就自动进入当前 Host 的 `READ_TOOLS` 或 `tool_allowlist`；应先设计最小、明确、可审计的新增只读准入机制，守住默认拒绝。这是旧 Step 1–3 设计与 PR #30 新 Host 模型之间的关键再审点。

完成首个完整、可验证的纵向切片，再处理固定 MCP 目录和现有工具的渐进迁移；不开展跨域重构/重新训练分类器。

## 6. 进入 Step 4 的前置检查

- [ ] 旧分支 Step 1–3 方案与此合同对齐，经用户审查确认；
- [ ] 以最新 main 的精确 SHA 建立 S25-05 设计/实现基线，不能把旧设计分支直接覆盖 main；
- [ ] 分清旧 Legacy 执行与 Unified 的单 Primary 语义所有权；
- [ ] Pydantic Schema、Adapter、MCP 版本映射、异常与审计合同的可测试接口固定；
- [ ] 只读扩展和写入扩展的 Host 准入权限分别定义，首轮只实现新只读工具；
- [ ] 用户明确授权进入 Step 4；S25-05 的实现、验收、合并各自独立授权。

**结论：05 的架构前置合同已形成；本文件不证明 Tool Registry 已实现，也不证明旧设计已经过新基线审查。**
