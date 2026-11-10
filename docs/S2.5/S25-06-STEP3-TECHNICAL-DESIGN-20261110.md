# S25-06 Step 3 — 版本化依赖计划与串行执行详细技术设计（修订 v2）

- 修订日期：2026-11-10
- 审计基线：`main@9e2e79a473089be47d25d1c10b7a9fa82f06d89b`
- 依赖：Step 1 基线审计、Step 2 行为合同、Step 2 复审、`S25-06-STEP3-FEASIBILITY-REVIEW-20261110.md`
- 本稿目标：逐项收口 F1–F5 的设计阻塞，建立可被 Step 4 切片实现的事务/授权/版本/证据合同
- 状态：**REVISED DESIGN / AWAITING SECOND PASS REVIEW — NOT IMPLEMENTED**。文档和静态检查不是物理运行证据。本轮不变更生产代码、数据库或默认开关。

## 0. 明确的设计决定

| ID | 决定 | 放弃的方案 |
| --- | --- | --- |
| D1 | 保留唯一 HTTP Host → 原 M4 Claim/Dispatch/Receipt/Checkpoint 主执行责任；新格式只提供依赖图解析、受信参数绑定与串行 step runner | 第二套 Runtime/第二个路由 owner |
| D2 | **同一 Turn 内逐步骤原子 checkpoint**，在每次 checkpoint 后重新读取可信上游证据再分发下游 | 全部步骤只在 Turn 末尾 checkpoint；每步新起 Turn |
| D3 | Claim 的受保护 snapshot 与 PlanStore CAS **在同一数据库事务一起滚动**；Guard 使用当前授权期望值，不再永久使用 preturn snapshot | 更新计划但不更新 Claim 导致下一步 guard 失效 |
| D4 | 保存不可变参数模板与已解析执行实例的分离记录；完成根据唯一 step/dispatch ID + digest/版本确认 | 用工具名+参数的猜测标记步骤完成 |
| D5 | 先用受信本地 MCP `definition` → `text_stats.text` 的只读数据链路；需要显式新版计划工具适配与 Host grant，不能直接继承 ordinary Registry 授权 | 将 `legacy-plan-v1` 静默升级为 Registry `1.0.0` |
| D6 | 旧/新格式在 API 所有生命周期操作分流；新格式 feature flag 默认为 false | 只在 loop 加一个格式判断 |
| D7 | 2–8 个步骤、单 Turn 逐项串行、失败即停止，继续遵守 M4 未知派发人工核实要求；本期不实现自主重规划、并行执行或写工具 | 一次升级全部 Agent 能力 |

## 1. 范围及与 S25-07/08 的边界

06 覆盖受用户当前请求支持的多步分解、DAG 有效性、执行先后、有限且可信的 Observation 输出引用、执行证据、同 Turn/跨 Turn 继续及安全中断；07 负责基于 Observation 主动改写计划、局部失效传播及重新选工具；08 负责验证目标是否充分达成和交付质量。**工具成功不等于用户目标已充分达成**。创建计划零调用、首步最多一次工具调用、resume 可连续执行本轮授权的剩余计划步骤，但执行范围不得扩大。

## 2. 数据契约：显式版本与双层绑定

### 2.1 版本定义

- `plan_schema_version = "dependency-plan-v1"`：仅标记新计划格式/依赖规则。
- `plan_executor_version = "dependency-plan-read-v1"`：标记新保存计划 read-only 执行协议（**设计中的新版本，不存在于 main**）。
- 每个 step 的 `tool_name` + `tool_version`：固定实际 adapter 实现版本；首版新依赖执行器允许与受信 Registry 的具体 adapter 版本号 `1.0.0` 对齐，但只有 **new plan Host grant + pinned adapter + exposure + args schema 全部检查通过**才可执行。版本相同不代表自动授权。
- legacy 保存快照（缺少 plan_schema_version）仅交由现有 `PlanLedger.restore` + `legacy-plan-v1` 路径处理；历史没有 tool_version 的旧快照仅在现有 legacy compatibility fence 内执行；显式 NULL/混合版本、未知新格式均 fail closed。
- 不能用旧版 `plan_versions_compatible` 校验新格式，也不能把新 checkpoint 送入旧 `PlanLedger.restore`。

### 2.2 新计划模板（设计 Schema）

```json
{
  "plan_schema_version": "dependency-plan-v1",
  "plan_executor_version": "dependency-plan-read-v1",
  "revision": 1,
  "next_step_id": 3,
  "steps": [
    {
      "id": 1,
      "goal": "查询 RAG 的定义",
      "depends_on": [],
      "status": "pending",
      "expected_tool": "mcp_reference_lookup",
      "tool_version": "1.0.0",
      "argument_bindings": {
        "term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}
      }
    },
    {
      "id": 2,
      "goal": "统计查询到的定义文本字数",
      "depends_on": [1],
      "status": "pending",
      "expected_tool": "text_stats",
      "tool_version": "1.0.0",
      "argument_bindings": {
        "text": {"kind": "observation_ref", "source_step_id": 1, "field": "definition"}
      }
    }
  ],
  "executions": {}
}
```

字段名属于 **Step 4 实施提案**，不是当前已实现数据库接口。所有列表最大 8 步、结构严格拒绝额外键；step ID 单调唯一，依赖必须是已存在较小 step ID，以强制计划保存时拓扑有序。若目标无法在用户已提供信息中确定受信 literal 或依赖，则返回澄清/不可创建，不能拼接模型猜测的字段。任务 goal 是自然语言，不发放工具权限。

每个完成步骤的 `executions[str(step_id)]` 必须记录：
`{dispatch_id, step_id, plan_revision, tool_name, tool_version, resolved_arguments_digest, source_evidence_refs, completed_at_checkpoint}`，其中 source_evidence_refs 至少含 `source_step_id, source_dispatch_id, source_tool, source_tool_version, source_field, source_arguments_digest`。不把原始敏感工具内容放进计划，也不由模型写 `executions`。可不复制完整已解析参数到 checkpoint，但实际参数必须以 `PlanToolDispatch.arguments_json` 为唯一具体值记录，并由 digest/严格比较连接模板与派发；提供由运行时绑定校验器确认字段来源的受信证明。若想存新表/列必须先输出迁移/回滚方案；可优先使用现有 `ConversationPlan.snapshot` JSON + 已有 Dispatch/Attempt 记录，待物理核验确定是否足够。

### 2.3 模板、运行实例、授权三者严格分开

- 模板：模型提案后由 Host 校验，用户审核并保存；`argument_bindings` 在执行后仍不可变。
- 运行实例：Host 从已 checkpointed 可信结果解析，产生一次且有明确 step_id 的 concrete arguments，执行前验证工具 Schema、长度、字段 allowlist 和可用版本；含 source receipts 的 digest/identity。
- 授权：每轮当前用户 scope + owner/Turn/Request + M4 Claim + Host 工具 allowlist 与单次绑定，不能因模板或 `READ_REGISTRY` 定义存在而自动授予。
- 对同一个步骤不得在没有显式修订及独立授权时重新派发；`uq_plan_dispatch_turn_step` 和全会话 unresolved fence 都必须保留。

## 3. 新计划校验与安全性

建议新增 `agent/dependency_plan_schema.py` 的 strict Pydantic Schema 和纯校验函数：
- `parse_new_plan`：显式版本、字段类型、2–8 steps、唯一 id、严格有序、合法 status、无外部伪造 executions。
- `validate_dependency_graph`：拒绝未知/自身/重复/后向不允许依赖、循环、跨 revision 的失效引用，避免负载过大和死循环。
- `validate_binding_graph`：每个 observation_ref 必须指向显式前驱；literal 必须锚定本轮用户 quote；源字段在 source tool+version 的输出白名单，目标参数在 target tool+version 的 Pydantic Schema 中存在且类型相容。计划创建时只验证可实现性，不预先编造引用值。
- `ready_selector`：读取当前持久事实，按原始位置选择第一个 pending 且已满足所有上游 checkpoint 的 step，返回候选不是授权。
- 来源和结果始终当作数据而非执行指令。禁止自由文本模板替换、任意 JSONPath、跨用户/会话访问及模型生成的 receipt。
- 建议错误码：`dependency_invalid_schema`, `dependency_cycle`, `dependency_unknown_step`, `dependency_not_ready`, `dependency_untrusted_observation`, `dependency_observation_truncated`, `dependency_argument_type_mismatch`, `dependency_version_mismatch`, `dependency_execution_claim_conflict`。

## 4. F1 收口：同一 Turn 逐步骤原子提交

**事实基线：** 旧 `agent/loop.py` 在一 Turn 中依次运行各 pending；`record_receipt` 单独持久化 observed；`apps/api/main.py` 在 Turn 末尾统一 CAS checkpoint、`checkpoint_in_session`、S25-03 projection。本节明确新增 new-format 专属步骤中间提交，旧路径原封不动。

### 4.1 执行顺序与锁定范围

1. Turn 创建时捕获原始 `preturn_plan_raw` 和 owner。Host 确认 `plan_resume` 或 `plan_execute_first`；新计划入口经过新格式/开关/工具集合/当前 scope 严格校验，未知格式零 Dispatch。
2. 原 M4 Claim 在数据库事务中绑定 `turn_id + expected_raw`。建立 new-format 的 **Turn-local current_raw**，初值为 `preturn_plan_raw`；只允许 trusted Host runner 修改。
3. 选取待执行步骤时从 current_raw 所对应的持久 Plan 及 checkpointed receipts 计算 ready。用户批准 first 时仅选择列表中首个未完成步骤；首项无法 ready 时返回 blocked，不自动执行前置依赖。resume 稳定逐项执行，任意失败立即停止。
4. 在真实工具调用前，使用可信 source receipt 解析 concrete args。通过当前 Turn 绑定、Claim snapshot、plan revision、工具允许列表、具体 Schema/版本、预算校验。**Dispatch 之前重新检查 Guard**，并持久化 `dispatch_unknown`。调用工具后仅 `record_receipt`，此刻是 observed，不得供下游引用。
5. **Per-step finalization transaction**：必须按统一锁序（ConversationPlan row → PlanExecutionClaim → PlanToolDispatch → AgentAttempt/AgentTaskState），验证 `current_raw` 与 PlanStore snapshot 完全相等、Claim 的 turn_id 和 snapshot 精确匹配、Turn 仍 `running`、该 Dispatch 的 receipt 为 `observed` 且 `ok=true`、具体 args 与 pinned binding/digest 相等，核查上游合法来源。只标记当前 **step_id** completed，保存对应 execution record，生成 new_raw。
6. 在同一 transaction 内通过 CAS `current_raw -> new_raw` 更新 Plan 快照；对持有者同事务滚动 `PlanExecutionClaim.snapshot: current_raw -> new_raw`；将当前 Dispatch 从 observed 转为 checkpointed，并写入/更新所需 AgentAttempt 和 TaskState/Transition。**任何一项失败则事务整体 rollback**，不得把 observed 结果当 checkpointed 使用。
7. 事务 commit 后仅由 Host 更新 Turn-local `current_raw = new_raw`。重新读取数据库验证相等，下一步 Guard 基于最新滚动快照和 Claim 比较；不再闭包永久使用 preturn_raw。此时才能读取刚完成的 source Receipt 并生成下一步 concrete args。
8. 所有步骤完成/阻塞后，Turn 最终化事务保存消息、终态、错误码和 Claim 释放。**终局提交不得再次重放已完成的 step finalization 或旧版全量 checkpoint 比较器。** 对新计划采用已经 checkpointed 的最新持久快照，最后 CAS/验证当前 head；原 legacy 分支继续使用原有末尾 finalizer。整个 Turn 对外只有最终回答，不对外提前声称整个任务完成。

### 4.2 必需的最小接口（仅设计，尚未实现）

- `ConversationPlanStore.advance_claimed_snapshot_in_session(db, *, owner_turn_id, expected_raw, replacement_snapshot)`：必须在锁住 Plan 行后检查并更新 Claim 的 `snapshot` 和计划快照；确认对应 Turn running。不可复用旧 `compare_and_swap_in_session` 后单独更新 Claim。
- `PlanDispatchJournal.checkpoint_step_in_session(db, *, dispatch_id, resolved_binding, checkpoint)`：只改当前已验证 Dispatch，保留 legacy `checkpoint_in_session` 原行为；验证唯一 step、具体 args 与源证据及版本。
- `DependencyEvidenceProjector.record_step_in_session(db, ...)`：只对新格式，以当前 dispatch_id 插入一次 AgentAttempt、TaskState transition；严格幂等条件为：已存在完全相同成功记录则 no-op，存在冲突则拒绝；不复用现有 `project_finalized_turn` 的“遇到已有 Attempt 立即报重复”机制。
- `DependencyRunner` 由唯一 AgentLoop/Host 调用；不得接受客户端传入 Claim/grant/receipt；支持 current_raw 的原子滚动及执行范围预算。

**额外关键点：** `PlanExecutionClaim.snapshot` 当前在获得 Claim 时固定为 preturn raw；现有 `saved_plan_execution_guard` 也始终核对 preturn raw。**两处都必须对新格式使用受信滚动快照，同时让原版继续锁定旧快照**。不得只修改其中一处。

## 5. F2 收口：实际可行的 source/target 工具组合

首版选定：`mcp_reference_lookup(term="RAG")` 输出 `definition` → `text_stats(text=definition)`。

此选择不是认定今天的 M4 已支持该闭环；而是确定现存两个受信 Adapter 和 Schema 可组成严格只读数据流：
- 上游：`agent/tool_registry.py` 的 `mcp_reference_lookup@1.0.0`，必须由 **受信本地 `StdioMCPClient`** 实例和固定 trusted reference 调用；拒绝动态远程 MCP tool/command/schema。
- 下游：`text_stats@1.0.0`，`TextStatsArgs.text` 长度 1–4000，输出字符数与词数。
- 当前 `agent/plan_read_invoker.py` 是 legacy，`agent/tools.py:SCHEMAS` 没有 `text_stats`，`Primary ProposedStep` 也没有这一选项。因此新增 **new dependency plan read adapter**，基于受信固定 `READ_REGISTRY` 的定义与 version-bound invoke 进行明确能力接线；每次调用必须有 Host 专门的 `plan_read_grants`/exposed_names，**不得复用 ordinary read 的 grants**。
- 新 `plan_executor_version = dependency-plan-read-v1` 表示以上 Host+Adapter 的执行约束，不替代各个工具的 `1.0.0`。旧 `legacy-plan-v1` 的 `PlanReadInvoker` 保持原行为和旧工具版本。
- Primary 的新 plan_create 提案 **采用显式版本的 step schema**，容纳有限工具与 observation_ref，不把 `text_stats` 直接塞进当前旧 `ProposedStep` 的 Literal、影响 legacy 路由。仍共用一套 Primary 语义决策，不引入第二次分类器裁决。
- 新 schema 与新 adapter 同时受默认关闭的 06 feature flag 管控，未启用时零 Dispatch、零新格式创建。

## 6. F3/F4 收口：可信证据解析、来源完整性与类型校验

建立 `services/dependency_observation.py`（只读）与单步 `ResolvedPlanInvocation` 数据合同：

- 查找时必须绑定当前 owner（经 Host 在 Conversation 校验）、conversation_id、current revision、source step_id、source dispatch_id、source tool/version/args digest；Dispatch 必须是 `checkpointed`、receipt_json.ok=true、存在对应成功 AgentAttempt；Checkpoint executions 中必须保留与之完全匹配的来源 ID。
- 来源字段首版仅 `mcp_reference_lookup.definition`，从 **已持久化的 bounded M4 Receipt** 读取，不把请求模型的文本、MCP 非白名单原始响应或普通 UI Evidence 替换成可信输出。
- `bounded_projection` 若原字段超过 1200 序列化字符，会只存 `definition_availability=truncated`；此时必须返回 `dependency_observation_truncated`。若字段不存在、不是非空字符串、目标值超过 `TextStatsArgs.text` 的 4000 上限或数据类型不匹配，同样 fail closed。不得默默截短并继续计数；不得从 `value` 回退重建 `definition`。
- 具体调用记录至少绑定：`plan_revision`、`step_id`、`dispatch_id`、`tool_name`、`tool_version`、`resolved_arguments_digest`、`source_evidence_refs`。保存原始 literal 引用模板不变；具体 arguments 存在受信 `PlanToolDispatch.arguments_json`，执行前严格 Schema 校验。
- `AgentAttempt.result_projection` 是有限展示数据，不代表完整的、可作为任何其他工具参数的 payload；只有来源字段满足 plan 输出 allowlist 和完整性合同才能向下游传递。
- 只有当前 plan revision 支持的历史来源证据可引用。首版新计划 **不支持 revision 改写**（除执行状态推进），新格式 `plan_revise` 返回明确暂不支持，不把旧改写函数应用于新快照。
- 前序 Observation 即使 `ok=true`，若依赖合同要求的字段缺失，也不能将目标步骤或整个计划标为已完成。

## 7. F5 收口：API 全生命周期版本分发矩阵

所有读取现有 checkpoint 的 HTTP 路径必须通过唯一 `plan_format_dispatch`（形式为 Host 纯函数，非新语义 owner），按**显式** plan_schema_version 做 fail-closed 分支。特别需要审计 `apps/api/main.py` 内多处 plan early-return/Review 路径，不能只改新增 route。范围至少涵盖：

| 操作 | Legacy (无 plan_schema_version) | New dependency-plan-v1 (flag on) | Unknown/new flag off |
| --- | --- | --- | --- |
| create | 使用现有建计划和版本 pin | 使用新 Schema + Host 验证，新格式唯一创建路径、零 Dispatch | deny，禁止回落 legacy |
| execute_first | 原已存 plan 首个 pending | 原顺序首个未完成 ready 步骤，最多一个 Dispatch | deny，零 Dispatch |
| resume | 旧 pending 线性 | new Runner 串行逐步 durable commit | deny，零 Dispatch |
| revise | 旧原路径 | 明确暂不支持/拒绝，无写入 | deny |
| cancel/withdraw | 原 Claim/Pending | 授权/Claim 门禁后取消，尚在运行禁止抢夺 Claim | 保守拒绝执行，删除须依既有授权且无法运行 |
| read/status/history | 既有格式 | 经 owner 验证的安全只读投影；不可误入 PlanLedger.restore | 可显示不执行的版本错误/有限状态 |
| recover/reconcile | 原 M4 审核流程 | 根据各 step Dispatch 的 observed/unknown/checkpointed 准确核实；无自动重放 | deny execution |
| finalization | 旧单 Turn 一次性 CAS、旧 projection | 只校验最新滚动 snapshot，并写终态/消息/释放 Claim；不重复投影 | 稳定拒绝 |
| rollback | 保留原数据 | 关闭 flag 后新计划只读受控，禁止丢给旧 executor | deny execution |

还要确保 `plan_create` 的模型路由/Host 事实一致，不能让一次用户请求既被旧 review 又被新创建器保存两个 Plan；新格式主动禁止普通写工具及任意远程 MCP。所有未知格式、未来版本、混拼步骤与显式 NULL 版本均不应执行。

## 8. 错误和崩溃恢复矩阵（针对新增 per-step 边界）

| 崩溃/故障点 | 可观察持久事实 | 恢复策略 |
| --- | --- | --- |
| Host deny / 还没 Dispatch | 无 Dispatch、无新步骤完成 | 报拒绝，可重新授权 |
| Dispatch begun、工具调用前或调用中崩溃 | dispatch_unknown | **不可自动重放**，M4 reconciliation |
| 工具成功但尚未 record_receipt | dispatch_unknown | outcome unknown，禁止自动继续 |
| receipt observed、per-step transaction 前崩溃 | observed、无 checkpointed | 禁止下游引用，先审核；不可视为可安全重试 |
| per-step transaction 任一步失败 | 整体回滚：Plan/Claim/Dispatch/Attempt/TaskState 保持原状态 | 无下游分发；按 unresolved/Claim 恢复 |
| per-step commit 成功、Host 崩溃于下一步前 | Plan 与 Claim 已滚动、receipt=checkpointed、Attempt/Transition 成功 | 不再执行已完成的步骤；核实 terminal/abandoned owner，保守继续新一轮授权/人工恢复 |
| 后续步骤开始后崩溃 | 该步 unknown/observed，前步 checkpointed | 只核查未决步骤，禁止对其自动重放 |
| 用户取消 / 并发 Turn 争用 | Claim 绑定当前 Turn | 不抢占 running claim、不覆写计划 |
| 最终答复保存失败但步骤已完成 | per-step commits 仍为真实完成 | 不虚构回滚；标记终结异常供审计，不重复执行 |

**额外约束：** 当执行期原 claim 所属 Turn 崩溃并成为 terminal/abandoned，现有 `acquire_execution_claim_in_session` 可以清理废弃 claim，但新格式必须先检查历史 unresolved Dispatch 和最新快照，并明确允许前缀已成功 checkpointed 的状态，不得因此推断所有后缀已执行。

## 9. 文件影响（建议逐切片实现，不拆成多条主链）

- Slice A：`agent/dependency_plan_schema.py`、`agent/dependency_plan_selector.py`，纯图/模板校验，版本矩阵测试。
- Slice B：`services/dependency_observation.py` + 完整性、Owner/Revision、M4 Evidence 绑定测试；对普通 Registry 只读数据支持边界进行验证。
- Slice C：`agent/dependency_plan_read_invoker.py`（或受限 Registry wrapper）、新计划 Host grant 与初始版本化 Primary Proposal；所有工具版本显式 pin，flag 默认关。
- Slice D：`services/plan_store.py` 原子滚动 Claim snapshot + CAS；`services/plan_dispatch.py` 新版 per-step receipt 验证；增量幂等 Evidence projection；在**唯一正式 AgentLoop** 的新格式分支中按当前 snapshot 串行调度；保留旧分支。
- Slice E：正式 HTTP、SQLite/PostgreSQL 物理、多 Worker 冲突、Crash/Recovery、真实 Qwen E2E、精准 HEAD CI 和独立复审。

**Step 4 最先实施 Slice A**：独立纯校验，不接生产默认主链；通过指定测试后再依序实施 B/C/D，避免在无事务证据情况下提前开放新计划。每个 Slice 保留原合同回归。

## 10. 可核验的验收清单和下一步

- DAG/schema 至少 10 个负例：未知依赖、环、自依赖、重复边、失效来源、错误来源工具、参数类型、非法版本、同工具同参重复步骤、非法修订。
- Host HTTP：create 零 Dispatch、first 最多一个 Dispatch、resume 串行依赖、拒绝/撤销、旧计划兼容、flag off/unknown 版本 fail closed。
- 事务物理：PostgreSQL 双 Worker、滚动 Claim/Guard、per-step CAS、Attempt 幂等、Receipt/Checkpoint 原子性、crash 各切点、重试不得造成无许可的第二次工具调用。
- Observation：真实 checkpointed MCP 短 definition → text_stats，证明计数输入字节与原受信字段一致；truncated/missing/跨 owner/revision 直接阻断。
- 真实模型：`ollama_proxy/qwen3:14b` 至少两种可执行依赖任务、每种不少于两次，以 HTTP/DB 的 Dispatch/Receipt/Attempt 为依据评估，不用模型自称成功替代。
- 对照：现有 S25-01~05 的路由、旧保存计划、Pending/Cancel/Write Guard 和 M4/PostgreSQL 基线不受影响。

**本稿已经提出针对 F1–F5 的具体方案，但仍需一次 Step 3 独立二次复审（尤其是 per-step transaction 和增量 projection 的安全性）。在复审通过前，不申请 Step 4 生产修改和 PR 合并。**
