# S25-05 — Step 1–3 新主链设计复审（2026-11-06）

**状态：DESIGN REVIEW COMPLETE / USER REVIEW REQUIRED / IMPLEMENTATION NOT AUTHORIZED。** 本文件是设计与代码接线方案，不是实现、测试通过的声明或 Step 4 开工许可。

- 权威起点：`main@d53e405a044c16eb53506724e4aa838865aa6956`，含 PR #30 Primary Routing Convergence 及 PR #31 前置收口。
- 历史来源：分支 `docs/s25-05-capability-tool-governance-design-20261022` 的 `docs/S2.5/S25-05-能力扩展与工具治理-前三步设计.md`（独立设计分支，**未合入 main**）。
- 已合并边界：`docs/S2.5/S25-05-PREFLIGHT-01-POST-MERGE-BASELINE.md`、`02-CONFIG-AUDIT.md`、`03-TOOL-INTEGRATION-CONTRACT.md`。
- 更改范围：**只修订 S25-05 设计合同**；不替换或自动合并历史设计分支，不删除 Legacy，不调整提示词，不改 Agent 主链，不新增数据库迁移。
- 有效技术范围：Tool/Capability Registry、可信 Schema 与 Adapter、执行授权交集、MCP 有限发现、版本/审计及兼容性；**不重复 S25-04 语义排序**、S25-12 审批工作流、S25-13 完整安全框架。

## Step 1 — 目标、范围和用户可观察行为

### 1.1 必须实现的能力

1. **可信扩展**：增加一个本地、无副作用的示例工具 `text_stats`，注册后从正式 `POST /conversations/{cid}/agent/messages` 进入已授权普通只读 AgentLoop，返回可验证的实际 Observation。新的只读工具不用在 `agent/loop.py` 的普通工具名称分支内重复实现一套调度。
2. **单一源定义**：工具的唯一名称、可信版本、Pydantic 入参类、提供给模型的参数 Schema、描述、effect 分类、能力标签、受信 Adapter、启用态在 Registry 定义；客户端请求/模型返回的工具名绝不用于 import 或动态代码执行。
3. **授权不能扩权**：主语义仍由现有 Primary 决定，Host 的 `disposition/execution_mode` 保持权威。Exposure 是交集，不是批准；Invoker 二次检查授权、版本、参数、当前会话工具许可及实时禁用态。
4. **既有工具不回归**：`calculate/utc_time/list_tasks/recall_memory/mcp_reference_lookup` 的普通只读行为、`create_task/save_memory` 的 Host 原子写入和同意、Pending 两阶段确认、M4 保存计划和 Legacy/Fake 兼容保持已有合同。PlanLedger 控制动作不自动变成第三方 Adapter。
5. **可治理 MCP**：MCP `tools/list` 仅被用来核对**事先获信任配置的本地定义**与远端实际可用性。服务器、启动命令、工具映射与版本兼容规则由 Host 信任的配置确定，不能由模型任意提供。
6. **稳定证据**：拒绝未授权/未曝光/无效版本/无效参数时没有 Adapter 调用；Observation、Attempt、Checkpoint/Dispatch 按现有约定保留，新增版本/来源元数据不泄露参数及用户隐私。

### 1.2 两个阶段性的工具能力域（新基线重要决策）

**范围 A（Step 4 首个纵向切片，强制）：普通只读工具扩展。** 使用 `text_stats` 完成 Registry → 受控 Exposure → NativeToolDecision → Invoker → 本地 Adapter → 真实 Observation/审计的全流程。允许新增显式 Host 只读准入策略，**默认拒绝未知工具**。不得因 `effect=read` 就自动对所有已注册工具开放。

**范围 B（同一 S25-05 的后续切片，先设计/证明，不在首片默认开启）：计划步骤工具可扩展。** `agent/primary_route_v1.py` 的 `ProposedStep.tool_name` 和 `BoundStep.tool_name` 当前只允许 `calculate/mcp_reference_lookup`；`agent/primary_host_admission_v1.py` 的 `READ_TOOLS` / `SCHEMAS` 还会再次校验；`agent/loop.py` 的保存计划执行与恢复另有固定白名单及 M4 Dispatch。**任何新增工具可进入计划都必须独立通过主语义 Schema、引用/来源绑定、Host、计划权限、版本与恢复幂等校验。** 首片 `text_stats` 可以支持普通只读，但不可声称“已经支持将新工具放入保存计划”。后续阶段必须给出该链路的正反验收，不得遗忘。

**写入工具扩展：本阶段默认禁止**。Registry 中声明 `effect=write` 或 `auth_policy_id` 不代表获得 Primary/Host 写权限；保留当前仅有 `create_task/save_memory` 的专用 Host 实现及 Pending 确认。新的写入权限需另行设计、单独授权。

### 1.3 必须继续满足的行为负例

- 模型在 `respond_only`、`read_only` 或被 Host 拒绝的执行范围内提议写入：**无执行副作用**；
- 未注册、未暴露、禁用、参数不合格、版本不匹配：稳定拒绝，Adapter 调用次数为零；
- 模型或 MCP 返回的“调用其他工具”“忽略安全限制”文本：只作为不可信数据，不修改 Host 许可；
- 普通只读新增成功，不代表计划步骤或 Pending 续接可以调用新工具；
- Legacy/Fake 不获得新的隐藏权限；旧兼容模式不能成为第二个 Unified 主语义审查器；
- 已准备/未知结果的调用不自动重试写入或有外部副作用的工具；
- 实际模型测试需记录不同措辞的成功/失败，不能以测试桩通过代替 Qwen 真实性能。

## Step 2 — 主线代码事实与原设计差异

### 2.1 复查的真实文件

| 文件 | `main@d53e405` 的事实 | 必须保留或处理 |
| --- | --- | --- |
| `agent/tools.py` | `SCHEMAS` 是手工 Pydantic 字典；`TOOL_SCHEMAS` 是另一个手工模型列表；`execute_tool` 按名称分支执行，MCP 只通过已接入客户端调用 | 改为受信定义/Adapter 单一来源，迁移期为旧导入提供不可变兼容视图 |
| `agent/skills.py` | 静态 `SKILLS`；部分 Legacy dynamic 路径使用关键词筛选，S25-04 已有 `select_tool_exposure` 等曝光逻辑 | Registry 只提供候选/元数据；保持既有选择行为的兼容，不新增关键词意图分类器 |
| `agent/loop.py` | `_tools()` 的普通工具调用在约第 344 行走 `execute_tool`；保存计划的 `run()` 在约第 569 行走第二次 `execute_tool`，恢复分支还有约第 617 行；`run()` 的 `read_only` 与 `tool_allowlist` 为固定名单 | 新 Invoker 不能只接一个调用点，至少覆盖普通/计划/恢复执行边界；每个路径保持已有 Host/Journal/Guard |
| `agent/primary_route_v1.py` | `ProposedStep` / `BoundStep` 的计划工具名是两个 Literal：`calculate`、`mcp_reference_lookup`；普通读仅提议 route/effect，不动态挑任意扩权工具 | Registry 上线不会自动改变 Primary 计划 Schema。范围 B 必须显式设计/验证，不能在范围 A 顺手拓宽 |
| `agent/primary_host_admission_v1.py` | `ordinary_tool` 普通只读可准入，写入只允许 `create_task/save_memory`；计划执行需要 `READ_TOOLS` + `SCHEMAS` 双校验；Pending `create_task` 与 MCP 查阅单独绑定 | Host 永远决定许可；新的只读类型需显式策略审查，不能仅靠 Registry `effect=read` 放行 |
| `apps/api/main.py` | Primary 结果落授权证据，再交 Host；普通只读 AgentLoop 接收 `read_only`，写入交由 `execute_primary_write_v1`；保存计划通过 M4 guarded claim/dispatch | 不把 Invoker 放到 Host 前执行；新工具不得绕过现有写入的原子事务/审计 |
| `agent/mcp_stdio_client.py` | 只允许 `mcp_reference_lookup`，启动仓库内 `agent.mcp_stdio_server`，完成初始化、`tools/list`、`tools/call` 的受限交互 | 保留固定可信服务配置和错误边界；增加目录核对，但不允许任意外部命令 |
| `services/primary_pending_confirmation.py`、`services/primary_ordinary_write_v1.py`、`services/write_effect_fence.py` | 一次性授权、事务写入、Attempt 和未决副作用围栏均独立存在 | Invoker 不能用普通 Adapter 模式替换它们，也不能自动重放未知结果 |

### 2.2 旧 Step 1–3 的具体修订决策

| 原设计思路 | 新基线裁决 | 理由 |
| --- | --- | --- |
| 受信静态启动 Registry + 有限 MCP Mapping | **保留** | 主线尚无统一可信工具定义，最小改造方向正确 |
| 一个统一 Invoker | **保留但明确边界** | 统一参数、曝光、版本、错误检查；Host 所有权、M4 Dispatch/PlanLedger 与写入事务不得被“统一执行器”吞并 |
| 新 `text_stats` 作为首个纵向切片 | **保留，限普通只读** | 无写入风险且可证明注册/执行闭环，不应自动扩权到计划 |
| 由注册表替换所有硬编码名单 | **修改：先显式授权、后逐步抽象** | 目前 Primary 计划 Schema、Host 的 READ_TOOLS、AgentLoop 的只读名单都属于多重安全门；直接删除是隐性扩权 |
| 恢复路径走统一 Invoker | **保留，先保留 M4 Journal** | `run()` 的已有保存计划/恢复路径不是“无状态普通调用”，有来源/崩溃后的执行含义 |
| 计划控制动作统一成普通工具调用 | **限制** | `propose_plan/revise_plan/complete_plan_step` 可以共享输入校验/错误包络，但状态修改只能交给 PlanLedger 和 Host，不能暴露给通用第三方 Adapter |
| `ToolDescriptor` 包含 effect/auth_policy_id | **保留但仅作为描述** | `effect=read` 非可信授权，`auth_policy_id` 也不产生用户批准 |
| Tool version + Catalog 快照 | **保留，补充版本恢复拒绝策略** | 旧 Checkpoint 绑定 `expected_tool` 与 `expected_arguments`，不等价于工具版本；可能需要补充安全兼容元数据 |
| 动态 MCP 发现 | **仅受信目录交集** | 远端 `tools/list` 不是允许执行的指令 |
| 旧 Step 3 声称 Step 4 可直接替换多份工具来源 | **拆成分阶段迁移** | 先提供兼容视图 + 跑旧接口回归，再移除重复声明，避免主链和测试大面积改动 |

### 2.3 明确的缺口与风险优先级

- **G05-A（P0）：新工具的 Host 显式准入策略与曝光交集**：否则“新增工具可选即执行”会违反 PR #30 的隔离约束。
- **G05-B（P0）：可信 Tool Definition/Schema/Adapter 一致性**：避免模型可见契约与后端校验漂移。
- **G05-C（P0）：三个执行调用点及 M4/Attempt 的相容接线**：不能只改变 `AgentLoop._tools` 就宣称统一执行。
- **G05-D（P0）：写入默认拒绝与 Legacy/Fake 的回归**：不得打破已验证的权限围栏。
- **G05-E（P1）：计划工具能力扩展的单独证明**：目前有 Primary Literal/Host READ_TOOLS/Checkpoint 三重固定限制。
- **G05-F（P1）：受信 MCP 映射/版本、出错分类和恢复一致性**。
- **G05-G（P1）：新增能力无关键词主意图分类器与真实 Qwen 任务效果验证**。

## Step 3 — 修订后的最小系统与实施合同

### 3.1 职责/数据流

```text
Authenticated conversation + user request + durable checkpoint
    |
    v
Primary semantic route proposal (one authoritative route owner)
    |
    v
Deterministic Host admission + existing durable authority checks
    |  denied/clarify/respond -> no tool effect
    |  authorized read-only execution scope + explicit tool permissions
    v
S25-04 candidate selection (only reduces trusted candidates)
    |
    v
Trusted ToolRegistry snapshot (read-only metadata / schema / version)
    |
    v
Turn-bound exposure = trusted enabled tools INTERSECT Host permissions
    | (semantic TopK may further reduce; never widen)
    v
NativeToolDecision -> proposed tool call (NOT authority)
    |
    v
ToolInvoker: verify [registered, exposed, version, args,
                     current Host allowance, tool-specific policy,
                     active Turn / checkpoint / dispatch fences]
    |
    +-- trusted local read adapter --------------------> bounded Observation
    +-- approved MCP adapter + server mapping --------> bounded Observation
    +-- existing Host-owned write/plan actions --------> specialized governed path
    |
    v
Existing AgentAttempt / PlanLedger / PlanDispatchJournal + Trace
```

**接口最小模型（设计型，不是可直接运行的 Python 源码）：**

```python
ToolDefinition(
    name: str, version: str, description: str,
    input_model: type[BaseModel],
    effect: Literal["read", "write", "control"],
    adapter_id: str, capability_ids: tuple[str, ...],
    policy_id: str, enabled: bool = True,
)
ToolRegistrySnapshot(
    digest: str, definitions: Mapping[str, ToolDefinition],
)
TurnToolExposure(
    conversation_id: str, turn_id: str,
    catalog_digest: str,
    allowed_names: frozenset[str],
    host_scope: Literal["read_only", "none", "governed_action"],
)
InvocationContext(
    conversation_id: str, turn_id: str,
    exposure: TurnToolExposure,
    host_grant: "server-owned trusted admission facts",
    active_version: str,
)
ToolInvoker.invoke(name: str, arguments: dict, context: InvocationContext) -> dict
```

*设计约束*：`host_grant` 和 `TurnToolExposure` 必须由服务端从可信决策构造，不由模型、HTTP 原始 body 或 MCP 返回值直接提供。敏感的用户标识/数据库 Session 不出现在 Provider 的工具 Schema 中。Invoker 可校验授权凭据，但**不自行签发 Host 权限**。

### 3.2 当前只读允许集合与扩展方案

**推荐 A：可信 Host tool-policy 表（默认拒绝），而不是注册即授权。**

- Phase 0：先保留现有静态集合不变，Registry 只为原工具提供稳定元数据/Schema/Adapter 映射。既有所有 P0 授权测试必须继续通过。
- Phase 1：针对 `text_stats` 引入明确的 Host allow-policy（例如 `ordinary_readonly_v1` 下允许的新名称），由 server-side policy 与 `Registry.enabled/effect` **求交集**；不改变计划的 `READ_TOOLS`。
- Phase 2：准备范围 B 的独立设计：必须同步考虑 `RouteSignal.ProposedStep/BoundStep`、`PrimaryHostAdmission`、`PlanLedger` 绑定和 `M4 Dispatch` 的安全升级。只有经过新独立专项物理测试才能将 `text_stats` 纳入计划工具。
- 新工具默认禁用或不允许执行直到 Host 明确放行；若命名或版本被撤销，活跃 Turn 不能继续沿旧快照派发。

**重要**：仅修改 `agent/loop.py:read_tools` 不足以获得 Host 批准；仅修改 Primary `Literal` 也不充分。

### 3.3 普通、保存计划、恢复的迁移顺序

1. **只读目录/Schema 影子一致性**：根据可信 `input_model` 生成 Provider Schema；与现有静态工具声明比较，保证参数/required/additionalProperties 契约兼容，临时保留旧 `SCHEMAS/TOOL_SCHEMAS` 只读投影给原测试或 Legacy。
2. **受限只读纵向切片**：`text_stats` 本地 Adapter 注册；Host 显式 allow；AgentLoop 普通 read-only 路径改调用 Invoker；验证 `prepared→succeeded/failed` Attempt 与观察值的真实来源。
3. **已存在工具的逐步迁移**：在不动写入和 PlanLedger 权限的前提下，让 `calculate` 等读工具走相同 Invoker；所有旧任务/记忆/普通读回归无变化。
4. **保存计划与恢复**：统一进入 Invoker 前，先保留 `saved_plan_execution_guard`、`PlanDispatchJournal.begin/record_receipt`、Checkpoint CAS、Unknown outcome 围栏；即使错误收敛，也不能丢失 M4 专有事务/来源保障。
5. **受信 MCP Catalog**：从现有受限 `mcp_reference_lookup` 连接开始，Adapter 显式 `service_id/local_name/remote_name/version/schema_digest` 映射，`tools/list` 仅核对；增加不受信字符串与协议负例，不启用任意服务发现。
6. **计划工具扩展另设验收闸门**：完成 Primary/Host/PlanLedger/M4 合同复审才能扩 `ProposedStep` 允许集合，不作为首个 `text_stats` 普通只读用例的隐含要求。

### 3.4 Tool Registry 与执行失败合同

- Registry 注册时：重复名称、无版本、非法 Pydantic 类型、缺少受信 Adapter、effect 与政策不匹配、模型 Schema 投影不兼容必须失败关闭。
- 执行时：`unknown_tool`、`tool_disabled`、`tool_not_exposed`、`tool_version_mismatch`、`invalid_arguments`、`permission_denied`、`mcp_timeout`、`mcp_protocol_error` 等稳定错误类别，不伪造已成功执行。
- 不泄露原始工具参数、模型提示词、MCP 命令行和密钥。MCP 响应文字只进入有限 Observation。
- 本阶段不引入自动权限审批；默认写入隔离沿用 `execute_primary_write_v1`、`commit_confirmed_pending_task`、`write_effect_fence` 的已验证事务。
- 版本兼容：新定义和历史 Checkpoint 绑定只按**被验证的**兼容表迁移；不存在映射则暂停恢复并给出诊断，不自动换工具。是否需要迁移 DB 由 Step 4 代码探查决定，不提前承诺零迁移。

### 3.5 最小可追溯验收矩阵

| 组 | 示例 | 必须可证明的断言 |
| --- | --- | --- |
| R1 Registry | 注册重复、空名称、未知 Adapter、Schema 不匹配 | fail-closed，现有定义没有半注册 |
| R2 Exposure | 新工具未 Host 批准/未模型曝光 | 零 Adapter 调用，稳定拒绝 |
| R3 Ordinary new tool | `text_stats` 真实 API，表达改写多样化 | Qwen 调用已授权工具；Observation 可复算；对应 Attempt 证据存在 |
| R4 Old tools | 5 只读工具 + write/consent + Legacy/Fake | 行为、错误码、写入次数、跨会话 scope 不退化 |
| R5 Plan/Recovery | CREATE/FIRST/RESUME，工具失败，M4 崩溃重启 | Guard/dispatch/Checkpoint 一致，未知结果不重复执行 |
| R6 Plan tool expansion | 新工具未获计划授权时模型/Checkpoint 强行选择 | Primary/Host 仍拒绝；不能以普通读成功掩盖计划扩展未实现 |
| R7 MCP | 远端列表差异、协议异常、冒充名称、注入文字 | 本地批准交集，拒绝不受信工具与危险指令 |
| R8 Governance | 删除/禁用/版本变化后继续请求 | 正确拒绝与可诊断结果，无未授权执行 |
| R9 Single owner | Unified AC1/AC2 fail-on-call 旧分类器 | 仍仅一个 Primary，不增加主意图分类器 |
| R10 Replay/Side effect | 同 request_id、Pending 确认二次提交、prepared writes | 409/限制/一致的持久证据，不发生重复写入 |
| R11 Provider | Fake + 真实 `ollama_proxy/qwen3:14b` | 列出实测模型、失败数、样本集与运行 HEAD，不以 mocked 结果冒充真实通过 |

### 3.6 Step 4/5/6 的明确范围门禁

**Step 4 不立即授权**；待用户审查设计后，在新实现分支按序开发 *可信 Registry → Host 显式只读许可 → 普通 Invoker 垂直切片 → 原读工具兼容迁移 → M4/恢复对接 → 受限 MCP*。每个步骤可以单独验收，而不是一口气推翻主链。新的写入工具/完全通用计划工具不在首轮实现的默认授权中。

Step 5：必须进行真实 HTTP 与真实 Qwen 验证、PostgreSQL 并发/断点恢复、S25-01–04 全量回归，并包含  R1–R11 的拒绝/兼容场景。

Step 6：记录准确的代码 HEAD、CI、模型版本、失败/恢复记录；用户批准后才可以 Standard Merge Commit 合入 main。未经授权不得合并。

## 本轮独立复审结论

**方案 C（可信 Tool Registry + 单 Invoker + 受控 MCP）仍正确；但只有在 Host 显式授权、普通只读与计划工具分开验收、写入事务继续由专用 Host Executor 持有的前提下才适配现有主链。**

本设计已完成基线核对和修订，等待用户审查。当前不宣称 S25-05 已实现，也不宣称所有工具动态扩展已经可用。
