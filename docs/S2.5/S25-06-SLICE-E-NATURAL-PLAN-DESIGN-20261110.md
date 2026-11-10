# S25-06 Slice E — 自然语言创建新版依赖计划：Step 1–3 设计与验收合同

**审计基线：** `main@64fe592df87b994bf73da210963c2e89561f8067`（PR #39 已合并、main CI 1453 passed / 5 skipped）。
**状态：** DESIGNING；本文只定义 Slice E 范围和实施门禁，不代表功能已实现。
**现有能力边界：** A = `agent/dependency_plan_schema.py` 的纯创建格式与来源/依赖校验；B = `services/dependency_observation.py` 的已提交 Observation 解析；C = `agent/dependency_plan_host.py` / `primary_host_admission_v1.py` 的确定性准入；D = `agent/dependency_plan_runner.py` 与 `services/dependency_*.py` 的受限 FIRST/RESUME、原子派发/回执/Checkpoint、代际围栏。当前 HTTP 测试使用预置 DB 新版计划；自然语言写入新版计划未接线。

## 一、用户可见的目标与非目标

用户可以直接说“先查询 RAG 是什么，再统计检索到的定义长度”，系统从**一次 Primary 模型提案**得到完整、带依赖的候选计划，Host 校验当前请求来源、工具/版本、依赖拓扑和参数引用，**只保存、明确不执行**。后续由用户另行发起“只执行第一步”或“继续剩余步骤”，继续复用 Slice D 的正式 Host 门禁与执行链，不增加第二套 Agent Loop 或二次意图分类器。

核心目标是自主生成可验证的计划结构，不是让 Agent 记住固定的测试话术。至少允许 2–8 步、严格 DAG、版本化只读 Tool、先前 Observation 的结构化字段引用；不存在可靠来源或能力不足时澄清/拒绝，不能制造虚假依赖。

**不在 Slice E 实现：** S25-07 失败驱动重规划、任意并行调度、通用写工具依赖、动态开放新工具、生产 IAM/Worker 终止证明、未知外部作用的自动清除；不自动合并旧 PlanLedger 格式，不把模型生成的计划视为授权。遇到不支持的工具/数据来源，只解释当前能力缺口。

## 二、审计缺口与复用矩阵

| 需求 | main 现状 | Slice E 动作 |
| --- | --- | --- |
| 新版依赖计划严谨 Schema | Slice A `parse_new_plan`、`validate_dependency_graph`、`validate_binding_graph` 已有 | 复用，不重写图检查 |
| 单一 Primary 语义所有者 | `primary_route_v1.py` 已有 `plan_create`，但 `ProposedStep` 限于 legacy 工具/参数，不能表达 `depends_on` 和 `observation_ref` | 向**现有同一次 Primary 结构化输出**增加显式版本化创建载荷，不并行调用第二个分类模型 |
| Host 决策 | `primary_host_admission_v1.py` 的创建候选分支已存在，但未授权新版依赖计划写入 | 新增严格的创建候选准入分支；只消费绑定且校验过的格式 |
| 原子保存 | `ConversationPlanStore.compare_and_swap_in_session` 已有无计划时 CAS 和 Claim 边界 | 精确要求 `expected_raw=None`，与当前 Turn 完成状态同事务落库；禁止覆盖旧计划 |
| 执行 | Slice D 已实现带 Generation 的 FIRST / RESUME | 原样复用；创建 Turn 不派发、没有 Attempt 和 Dispatch |
| 真实模型证据 | Slice D 是 Fake/预置计划及独立多进程验证 | 新增真实 Qwen 结构化 proposal、正式 Conversation API 两轮执行与失败样本 |

## 三、契约方案及取舍

### 3.1 首选：同一个 Primary Route 的可选、互斥、版本化 Dependency 创建载荷

在 `RouteSignal` / `BoundPrimaryProposal` 增加**可选** `dependency_plan`，仅 `route_kind=plan_create` 且对应 `plan_schema_version=dependency-plan-v1` 时允许，要求 `steps=[]`（原 legacy 步骤字段不得同时存在）。其他 route_kind 只允许 `dependency_plan=None`，避免混合创建/执行意图。保留 legacy `plan_create` 的原有 `steps` 契约，flag 默认 OFF 时行为与现有主链一致。

载荷使用已存在的 `DependencyPlanCreation` 严格模型，不再发明相近的 PlanSpec；建议让模型只输出实际步骤及引用，由确定性 Host 填充/验证固定元数据（version=dependency-plan-v1、executor=dependency-plan-read-v1、revision=1、next_step_id）并再执行 `parse_new_plan(..., source_message=exact_current_request)`。若要使用模型完整输出对象，则上述固定字段必须严格匹配且不自行修复工具、参数或引用。两者择一的实现原则：**唯一持久化目标始终为现有创建 Schema**。

**真实模型兼容门禁：** 先核查 `HTTPUnderstandingModel` 的结构化 JSON Schema、`model_json_schema`、Qwen3:14b 的输出与约束；不能以只通过 Fake 的新字段为由修改默认 Prompt。新增字段和 Prompt 必须随 S25-06 flag 明示启用，避免扩大默认主链模型输出的变化面。

### 3.2 绑定与安全

- 根路由必须是已绑定当前请求唯一 verbatim quote 的 `plan_create`，工具每个 `literal` 的 `source_quote` 与 `value` 必须来自**本轮用户文本**，拒绝从历史对话、工具文字、模型分析里冒充当前来源。
- `observation_ref` 仅引用此前节点的、Schema 允许的 `mcp_reference_lookup.definition`，不在创建时假装已经拿到真实 Observation；执行时仍由 B/D 证据链确定类型、完整性和可信性。
- 工具必须属于 Slice A 当前白名单且版本精确匹配；Host 不接受任意 MCP 文本指定的工具、版本或执行许可。
- 依赖图只支持按 ID 稳定拓扑顺序的 2–8 步；存在未知节点、重复边、自依赖、循环、前向引用、越权工具、缺参、断裂 source quote 一律拒绝或澄清，禁止以“自动猜参数”修复。
- 任何失败必须没有新 Plan、Dispatch、Attempt、Claim 或 Worker Fence；错误归因可见但不暴露原始模型输出或凭据。

### 3.3 原子创建事务

一个用户 Turn 首先由现有 Conversation API 创建，记录 immutable Primary 证据与提案。Host 只在：当前 Conversation owner 一致、Turn running、之前没有 saved plan、无 PlanExecutionClaim、格式与来源合法、feature flag 开启时生成可信候选。

在与 Turn 终结、Assistant Message 完成**同一个数据库事务**内调用 `compare_and_swap_in_session(expected_raw=None, snapshot=validated_plan.model_dump())`；处理竞争冲突为稳定的 `dependency_plan_create_conflict`，不可覆盖别的 Turn 保存的计划。创建成功后返回“计划已保存，尚未执行”，并可给出依据可信 Schema 的计划摘要。严禁调用 `READ_REGISTRY.invoke`、`PlanDispatchJournal.begin` 或分配 `DependencyWorkerFence`。

**特别注意：** 当前 `apps/api/main.py` 在旧 `plan_create` 结果上通过 `pin_new_plan_checkpoint` 写 legacy schema，Slice E 必须在该步骤前进行**互斥分流**，决不可将依赖计划提交给 `PlanLedger` 或 legacy pin/projection。复用既有 Primary 路由与当前 HTTP 终结，而不是另写一条第二 API 主链。

## 四、实现切面（建议一条渐进主线，不拆成许多补丁）

1. **E1 模型创建提案契约：** 同 Primary 路由增加 opt-in dependency-plan 字段、严格互斥与引用绑定；Fake schema 用例 + 真实 Qwen 结构化输出对照（先不写 DB）。
2. **E2 Host 与原子保存：** 启动 feature flag 后，扩展 Host 创建准入；同 Turn 事务 CAS 写计划、完成消息；默认 OFF、旧版 plan_create 保持兼容，无任何工具调用。
3. **E3 完整验收与收口：** 正式 HTTP：自然语言创建 → DB 原子持久化 → 新 Turn FIRST → 新 Turn RESUME → 跨轮真实 Evidence → 最终计划已完成；PostgreSQL 并发创建冲突、崩溃/错误无副作用、owner 隔离、旧版计划保护；真实模型 E2E 标明模型版本/采样和失败案例，完成 Step 5/6 后才请求合并。

## 五、必须通过的验收矩阵

| ID | 场景 | 核验 |
| --- | --- | --- |
| E01 | 新版自然语言任务生成 2–8 步依赖计划 | 1 个 Primary 提案；Host 接受真实 Schema；创建 Turn 无工具调用 |
| E02 | Create → FIRST → RESUME | 各轮 Primary 单一所有者；完整 Dispatch/Receipt/Attempt/Checkpoint；无重复执行 |
| E03 | source quote 不属于本轮 | 拒绝，数据库和执行证据不变 |
| E04 | 虚构/跨步/循环/错误字段/版本 | 拒绝，无自动修正 |
| E05 | 重复创建/两 Worker 同时创建 | 仅一个 CAS 成功，不覆盖原计划 |
| E06 | 旧格式计划存在或 flag OFF | 不进入新版创建支路，旧 M4 不回归 |
| E07 | 创建中异常/DB 回滚 | 无半提交 Plan、Turn completed 与 Plan 未对应的伪成功 |
| E08 | 用户试图创建并立即执行 | 分离操作；只创建不派发 |
| E09 | 真实 Qwen 变体提示、多种任务表述 | 记录成功率、误生成率、澄清和失败理由；不能硬编码测试语句 |
| E10 | PostgreSQL 正式 API 并发和跨轮恢复 | 与 Slice D 代际围栏、Claim 与旧投影兼容 |

验收报告必须记录 exact `main`/branch SHA、provider/model、温度或采样设置、Prompt/Schema 版本、工具版本、测试环境、CI、真实模型失败样例。Fake 通过不得写成真实模型完成。

## 六、发布门禁与下一步

- 默认 `s25_06_dependency_plan_enabled=False`；旧格式与既有 ordinary/tool/legacy 入口不变。新增实验分支仅扩展 opt-in 功能，不能激活生产恢复 API。
- 本设计通过独立审查前，不开始 E2 代码接线。先实现 E1 并审查与 `RouteSignal`、`BoundPrimaryProposal`、`propose_primary_route` 结构化输出的兼容性。
- 完成 Slice E 后，S25-06 才首次具备“用户自然语言创建→多轮执行”的完整窄域路径；是否将 S25-06 标记 CLOSED 需再以真实模型任务质量评测及本能力范围决定。失败驱动自主重规划属于 S25-07，不能为此把 Slice E 无限扩张。
