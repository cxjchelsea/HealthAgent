# S25-07 Step 3 — Observation 驱动重规划详细技术设计（2026-11-11）

> 状态：**DESIGN CANDIDATE — implementation NOT authorized**。仅文档，不改运行代码。
> 精确源基线：`main@e02845f7c3452ef452efceb20f7b725e5716529b`，设计分支 `audit/s25-07-step1-current-main-20261111`。
> 上位合同：`S25-07-STEP1-MAIN-GAP-AUDIT-20261111.md`、`S25-07-STEP2-BEHAVIOR-ACCEPTANCE-CONTRACT-20261111.md`、S2.5 V2、S25-06 Final Completeness Audit、Integration Handoff。

## 1. 设计决定与真实障碍

目标是复用**唯一**正式入口 `POST /conversations/{cid}/agent/messages` 和当前 Primary → Host → Dispatch/Receipt/Attempt/Checkpoint，增加**已授权只读范围内**的 Observation 驱动重规划；不新建 Runtime、Planner Server、第二套 Primary、生产恢复 operator、无界反思循环。

实码确认不可直接绕过的 5 个约束：
1. `agent/dependency_plan_host.py:_step_for_read` 强制 `revision == 1`、completed 为连续前缀、同版本 `executions`；`services/dependency_observation.py` / `services/dependency_execution.py` / `services/dependency_dispatch.py` 同样要求保存的 source_record.plan_revision 等于**当前** revision。
2. `services/dependency_dispatch.py:begin_dependency_dispatch_in_session` 禁止任何同会话、同 `step_id` 的重复派发（不限 Turn）；`PlanToolDispatch` 在 DB 里还有 `UniqueConstraint("turn_id","step_id")`。因此验证失败后重试时原 step_id 无法再次派发。
3. `services/plan_dispatch.py:UNRESOLVED_DISPATCH_STATUSES` 包含 `dispatch_unknown`、`observed`、`observed_failure`；S25-06 失败 `ok=false` Receipt 保留于 `observed_failure`，runner `blocked`，尚无可验证安全终结/继续合同。
4. `services/dependency_execution.py:_project_step_in_session` 只为成功 checkpoint 创建 `AgentAttempt(status="succeeded")` 并投影 S25-03 task state。可信失败 Receipt 当前没有相应失败 Attempt；不能假定 TaskState 已完整表达失败。
5. `apps/api/main.py` 只在既有 Primary/Host 准入后 CREATE / FIRST / RESUME；`PrimaryRouteEvidence` per Turn write-once，`dependency_plan_runner` 内部非独立授权入口；不能让一次失败后的新模型文本获得额外授权。

## 2. 最小架构及控制权

```
Current HTTP Turn
  → existing single PrimaryRoute (current request + Host-trusted checkpoint)
  → existing Host admission (immutable PrimaryRouteEvidence, owner/scope)
  → existing Claim / generation Fence / dependency runner
  → Dispatch [durable] → READ_REGISTRY → Receipt [durable]
  → trusted failure classification, Host-built bounded evidence view
  → same Primary decision capability, typed ReplanProposal (not new router)
  → Host ReplanAdmission (narrowing existing permit only; no privilege escalation)
  → atomic plan CAS + evidence lineage + task transition
  → if original permit explicitly covers continuation AND safe budget:
       new guarded Dispatch in same scope
    else:
       partial / replanned_pending_execution; next user Turn through existing Primary+Host
```

**授权原则**：ReplanProposal 是建议不是 permit；`plan_resume` 的当前执行授权可被 Host *收窄*成一次相同只读范围的修订/受控重新派发，**不得自动扩大**到其他工具、写入或本来未授权步骤。若当前已准入权限未覆盖重派发，必须**先记录修订、终结 Turn、下一轮重新准入**。不能通过对 PrimaryRouteEvidence 更新 route_kind 或替换其不可变 preturn digest 来制造权限。

优先采用**同一语义 Primary 组件的后置 Observation 决策调用**（不是新的二级分类器）：后置决策仅根据当前原用户目标、可信计划、已核实 Observation 摘要和已授权工具清单，产生结构化 `ReplanProposal`。新决策是否同 Turn 自动继续由 Host 判断，不能由模型决定。

## 3. 建议契约结构（Step 4 可细化，不能随意改变核心不变量）

### 3.1 Host 生成的 ObservationFact（输入给 Primary 的白名单视图）
- `conversation_id`、`turn_id`、`step_id`、`plan_revision`、`dispatch_id`、`receipt_status`（`verified_success / verified_failure / unknown / host_denied / conflict`）、`error_code`、`retry_class`（`safe_candidate / correction_required / nonretryable / indeterminate`）、`verified_evidence_refs`、`remaining_budget`。
- 仅 Host 可查询、校验并生成上述事实；工具的 `error` 字符串只能作为诊断输入，不能直接决定 `retry_class`、用户意图或授权。
- `verified_failure` 必须满足：dispatch 归属、匹配 owner/conversation/Turn/step/版本、Receipt `ok=false`、来源与 arguments digest/版本及 worker fence 一致，且无可信成功 checkpoint；`unknown` 坚决不自动执行或声称已失败。
- 模型只见必要摘要和有限内容；不泄露原始 MCP 文本、用户隐私、令牌、内网异常或完整堆栈。

### 3.2 Primary ReplanProposal（纯数据）
```json
{
  "schema_version": "replan-proposal-v1",
  "action": "continue | retry | revise_pending | replace_tool | clarify | stop",
  "expected_plan_revision": 1,
  "target_step_id": 2,
  "source_dispatch_id": "uuid-or-null",
  "reason_code": "bounded enum",
  "proposed_pending_steps": [],
  "needs_new_authorization": false
}
```
- 示意结构，不是当前仓库已存在接口。严格 schema/长度/动作-字段一致性；`reason_code` 不能凭自然语言产生 Host 权限。
- 不允许模型提交 `completed`、`executions`、`AgentAttempt`、`Receipt`、`Claim`、`Fence`、`owner_id` 等权威字段。
- `proposed_pending_steps` 是**整个未完成后缀的显式提案**，不能用部分 JSON patch 隐式保留不合法依赖。参数 literal 必须绑定本轮可信用户输入或经 Host 保存的原始引用；不得把模型推测当用户原文。

### 3.3 两种 revision 不可混淆
- `plan_revision`：计划每一次成功修订 +1。
- `AgentTaskState.revision`：状态投影每次成功更新 +1（不是 plan revision）。
- `Dispatch` 的 attempt identity 由 `dispatch.id` 唯一确定；老成功证据保存其**产生时** `origin_plan_revision`，不能在新计划里把旧 revision 改写为当前数字。
- `PrimaryRouteEvidence` 一次 Turn 一条 immutable；必要的 Replan 决策凭证可保存**受限、不可修改**的附加审计事件，引用原 Primary evidence/Turn/Plan CAS，并不创建第二条“主路由”。

## 4. 计划 schema 兼容与跨版本证据继承

**兼容建议**：维持 `plan_schema_version="dependency-plan-v1"`、`plan_executor_version="dependency-plan-read-v1"` 的**已保存历史快照只读解释**；新修订运行格式要么引入明确的 `dependency-plan-replan-v1` executor marker、要么增加严格且可区分的 revision/lineage 分支。**首选新增 executor version**（如 `dependency-plan-read-replan-v1`）但不要更改原先已保存 revision=1 记录的身份。Step 4 前需核对所有老 snapshot 分类/HTTP 回归对版本的反应。不能简单把原 `DependencyPlanCreation.revision: Literal[1]` 改成可变数字，因为创建与恢复应是不同 schema。

原计划完成步骤的 `executions` 记录继续保留 `plan_revision`（它是产生证据时的 revision），新增受信修订 lineage 显式允许 `source_origin_revision <= current_revision` **且**验证该 completed step 的工具、版本、字面值/参数来源、dispatch、Receipt、Attempt、arguments digest、依赖未变。仅比较数字小于等于绝不够。须检验每个 lineage 边的父 checkpoint digest/源 step identity 与保持不变的绑定；如果已完成步骤或其源依赖不再符合新目标，应阻断直接保留，安全澄清或提出新任务。

**局部修订限制**：只改 pending 后缀；原 completed 前缀及其严格可信 binding 不变；新后缀的任何 `observation_ref` 必须仍指向被验证保留的 completed source 且字段/类型相容。维持顺序执行上限 2–8 步、只读两工具、无环、无悬挂依赖；不为本阶段实现跳跃/并发任意拓扑。

## 5. 失败 Attempt 与安全重试：执行日志设计

### 5.1 必须解决的历史约束
- `observed_failure` **不等于** `dispatch_unknown`，前者已有受信失败 Receipt，但当前属于 `UNRESOLVED_DISPATCH_STATUSES`；后者结果未知。
- 成功 `checkpointed` 保留现有强校验；失败 Receipt 经核验后需要**单独原子终结步骤失败尝试**（建议 transition 到新的终态如 `failure_checkpointed`，名称需 Step 4 检查迁移兼容），同时保存 `AgentAttempt(status="failed", error_code)` 及 bounded projection + S25-03 state transition；**step 本身仍 `pending`**。
- 不直接更改 `record_receipt` 为自动成功，不删除旧 Dispatch，不让未经核实的 `observed_failure` 解除 unknown fence。
- 失败终结事务同样验证原 Turn、Claim、Fence、receipt/dispatch 归属和计划 CAS；在未知结果/Receipt 不一致时不终结，交给现有独立恢复工作流。

### 5.2 不能通过“简单删除 duplicate guard”实现重试
- 当前 `PlanToolDispatch` 的 `(turn_id, step_id)` UNIQUE 及 `begin_dependency_dispatch_in_session` 的会话/step_id 全历史阻断必须做**受控、版本兼容的变更**。
- 首选为同一 logical step 引入**单调 attempt ordinal / dispatch attempt identity**（若确需同 Turn 重新派发，迁移唯一键为 `(turn_id, step_id, attempt_ordinal)` 或由已存在 `dispatch.id` 独立标识，并以 DB 行锁/CAS 保证唯一顺序）；历史数据缺省 attempt=1。不能仅对 SQLite 改约束而不做 PostgreSQL Alembic 迁移。
- 对全会话同一 step 的历史派发，**只允许**：前一 Dispatch 为经验证的失败终态、没有未知/未完成 Receipt、没有该 step 成功 checkpoint、当前 revision/Claim/Fence 新授权且预算未用尽时再次派发；成功历史不可重放。
- 不推荐为重试制造“新 step_id”以规避旧 guard；那会损坏步骤 lineage 和 TaskState，而不是正确的 attempt 模型。
- 配置上限：当前执行 Turn 最多一次受信失败后的自动重派发/一次自动修订；同时持久化按目标/步骤/失败签名累计预算，防止每个新 Turn 刷新无限重试。预算数据来源必须 Host-owned。
- 真正的 `retryable` 需要可信系统错误分类（例如已证明工具未完成的可恢复瞬时错误）；普通 `ok=false` 不自动等于 retryable。若当前受信工具无法提供足够语义，R03 只能检验拒绝与澄清，不能谎称自动重试正向成立。

## 6. 原子事务、Lock 顺序和并发冲突

**计划修订只能发生在无活跃执行者对同一 snapshot 拥有未解决 Claim/Fence 的安全点。**

推荐最小两阶段（对跨 Turn 修订更容易独立证明）：
1. 运行中 Turn 收到失败 Receipt → 在自身拥有原 Claim/Fence 时经核验终结失败尝试，决定后续停/修订；如果修订与原 Turn 同事务域且可证明隔离，锁定计划并原子提交新 snapshot + task projection。否则终结 Turn 并释放已安全结算的 Claim，下一 Turn 重新准入修订。
2. 修订事务使用 `ConversationPlan`（FOR UPDATE）→ `PlanExecutionClaim`（FOR UPDATE）→ 已有 Fence（按原锁序）→ 涉及的 Dispatch/Attempt/TaskState → `Turn` 的稳定锁序；检查 owner, current Turn, Primary evidence, preturn digest, expected plan raw/CAS, old revision, executed prefix, no unresolved unknown，提交 revision 与 immutable revision evidence；更新 `AgentTaskState` 的 `plan_revision` 和 step projection，追加 `AgentStateTransition`，禁止第二套 task store。
3. 后续新执行由当前已合法覆盖的 scope 或**下一 Turn**重新 Primary/Host 准入，重新绑定新 raw snapshot 与新 generation；不能借用已终结 Turn 的旧 Fence。
4. 任何 CAS 失败 rollback 全部变更；失败请求不得悄悄沿旧计划继续派发。冲突后只能重新读取可信状态、重新取得授权。
5. 不复用生产级 `services/dependency_recovery.py` 的 operator-only reconcile 当作普通模型功能，也不以 heartbeat 超时证明 worker 已死亡。

**重点验证**：当前 `PlanExecutionClaim.snapshot` 会随着每个 checkpoint 一同更新；PrimaryEvidence 的 `preturn_snapshot_digest` 保留不可变的 Turn 入场快照。修改后两者不能机械要求相等；需依靠明确的 lineage / current expected_raw 及同一 Turn 原始许可的受限派生边界。

## 7. Primary / API 接线（只延伸现有路由）

- `agent/primary_route_v1.py` 继续负责用户当前意图并保持 `primary-route-v1` 单一 owner。新的 Observation 驱动提案可复用 Primary 结构化输出和 provider；不要增加未绑定用户语义的 keyword classifier。
- `agent/primary_host_admission_v1.py` 对版本化计划的修订有**新格式专用严格 allow**分支；legacy `PlanLedger` 不可作为 fallback。只有明确失败及来源验证时才可能准入自动修订，当前用户显式更正需直接来自当前 Turn。
- `apps/api/main.py` 复用 `send_agent_in_conversation` 的 Turn 创建、PrimaryEvidence write-once、Claim gate、最终事务。当前 `run_dependency_read_scope` 直接在失败时返回 `blocked` 且 HTTP 将其统一为 `plan_execution_reconciliation_required`；须区分可信失败与未知，在现有入口反馈正确 reason，不得统一贴成“结果未知”。
- 不能用纯新的 `run_replanner` HTTP 路径绕开上述入口，也不能允许 direct model output dispatch。若需要服务函数，定位为 Host 授权后内部调用。
- `READ_REGISTRY` 是只读工具实现契约，但 `approved_read_grants()` 管普通 ordinary_tool；版本化计划权限由 dependency Host 独立收窄。不得错误地以 Registry 注册即等于 Saved Plan 的执行许可。

## 8. 交付切片建议（Step 4 才开始）
A. 可信失败 / 未知分类 + failure attempt 原子投影 + 可观测响应；保持旧计划无回归。
B. 版本化 `replan` schema 与 Host 严格 validator + immutable lineage + 原子 CAS/TaskState（先只保存修订，不调用工具）。
C. Primary Observation 提案与正式 API 接线；真实改变未完成后缀，修订结果精确展示并需重新 Host 授权。
D. 受控继续与可证明安全的只读重试；必要时对 Dispatch uniqueness 做 Alembic PG+SQLite 迁移，严格测试旧历史数据。
E. 集中 PG 并发/崩溃、HTTP、Qwen 多次挑战及独立安全复审。

**切片是风险排序，不是强制 5 个 PR。** 每个合并动作仍需要用户明确 `AUTHORIZE MERGE #N`。

## 9. 准确验收 oracle
- Step 2 R01–R12；其中 R02（失败→合法后缀修订）、R04（completed prefix 跨 revision 验证）、R07/08/09/11（权限/未知/并发/注入拒绝）为必须证明的核心；R03 仅在真实可验证 retryable 条件下标为自动重试 PASS，否则按受限功能范围标 `NOT SUPPORTED`，不得用假数据宣称通用可恢复。
- 原已有 S25-06 CREATE/FIRST/RESUME、#47 M04 审计、S25-03 task projection 回归、legacy 及 ordinary 工具回归，不得损坏。
- 单测：失败 Receipt/Attempt/多次调用计数、源 revision 链、非法 DAG / source field、CAS 失败零副作用、版本切换、权限检查。
- 集成：正式 API 多轮、真 PG 两连接及多进程、崩溃四个边界、重复/迟到 Receipt、同一 step 受控重试、不同用户拒绝、关掉 flag 不改变旧逻辑。
- 真实 Qwen：模型做 **Observation-aware** 决策，而不是固定失败码模板直接选择工具；至少 Step 2 明列的 6 类挑战，每类 ≥2 次，以 durable evidence 为 oracle。
- 完成代码实施后 Step 6 只可申请 `CLOSED — bounded read-only Observation-driven replanning`，不能宣传通用 DAG、任意工具、生产部署或医学安全。

## 10. Step 3 设计审查清单
- [ ] 失败/未知是否绝对分离；失败 Receipt 能否安全终结成 FAILED attempt，且不更改 completed。
- [ ] revision/lineage 是否能由原始可信证据核验而不是模型字符串。
- [ ] 新的 `attempt_ordinal` 及 DB migration 是否确实必要、是否不破坏旧唯一键及 PR #44/#45 的 PG 合同。
- [ ] 同 Turn 自动推进是否真的被原 Host permit 覆盖；若否，先保存后下一 Turn 执行。
- [ ] 生产 worker-fence/恢复接口是否完全保持现有 operator-only 边界。
- [ ] 特性开启/关闭、新旧格式、旧数据库记录、TaskState 和 API 响应是否都有回归计划。

**结论：本文件为供独立复审的技术设计，不是 Step 4 实现许可。**
