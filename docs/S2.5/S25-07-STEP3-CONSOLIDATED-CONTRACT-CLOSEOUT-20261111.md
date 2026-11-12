# S25-07 Step 3 — 集中技术收口与实现准入合同（2026-11-11）

**审查性质**：基于真实 GitHub main@e02845f7c3452ef452efceb20f7b725e5716529b 的静态合同冻结；**没有实现或运行测试**。配套文件：Step 1 Audit、Step 2 Behavior Contract、Step 3 Technical Design、Step 3 Independent Review。PR #49 为 Draft，未获合并授权。

## 0. 总判定

**Step 3 的 P0 实现边界已收敛为分阶段设计合同，但 Stage-4 启动仍须用户审查认可本合同。** 优先交付：可信失败事实 → Host 受限计划后缀修订 → 原子存储 revision 与已完成证据引用 → 独立新 Turn 授权继续。**禁止**从第一切片就删除 dispatch guard 或引入无限自主重试；后续受控再派发必须经专门迁移/并发审查。

注意：Step 2 的 R03（同一步重试）为**后续受控切片的正向验收条件**，不是可默默删除的目标；如没有可信 retryable 样例，S25-07 只能按实际限定范围收口，不得称“通用重试已完成”。

## 1. 实码确认的 P0 事实

| 位置 | 事实 | 必须保持 |
| --- | --- | --- |
| `services/plan_dispatch.py:UNRESOLVED_DISPATCH_STATUSES` | `dispatch_unknown, observed, observed_failure` 都算未解决 | 无法核验失败 Receipt 时仍禁重派发 |
| `services/dependency_dispatch.py:begin_dependency_dispatch_in_session` | 同会话同 step_id 只允许首次派发；且原路由 PrimaryEvidence、Claim、Fence、snapshot digest 同时核对 | 不能仅删查询绕过授权 |
| `services/db.py:PlanToolDispatch` + `0009_plan_tool_dispatch` | 唯一键 `(turn_id,step_id)`；当前 Attempt 主键为 dispatch ID | 同一 Turn 同一步多次尝试要变更 DB 契约，涉及 PG/SQLite migration |
| `services/dependency_execution.py:_project_step_in_session` | 仅成功 checkpoint 写 `AgentAttempt(succeeded)`；失败停在 `observed_failure` | 失败 Attempt 不可声称已成功或完成步骤 |
| `agent/dependency_plan_host.py:_step_for_read` | revision==1、连续 completed 前缀和同版本 executions | 新修订格式单独校验；旧快照原样可读 |
| `services/dependency_observation.py` / `services/dependency_execution.py` | 已完成前驱 Receipt/Attempt/args digest 都与当前 plan_revision 对齐 | 不能只把校验改为 old<=new |
| `services/plan_store.py:compare_and_swap_in_session` | Plan 行优先锁、严格 expected_raw CAS、Claim owner 限制 | 不能在其他 worker 持 claim 时修订 |
| `services/dependency_fencing.py`、`services/dependency_recovery.py` | worker generation 非时间租约；operator recovery 双外部 verifier | 自动重规划不能调用 operator-only recovery 解除未知 |

## 2. 失败事实归档：冻结的状态转换

### 2.1 区分三种状态
- `dispatch_unknown`：已记录派发意图，**缺可信 Receipt**，执行结果未知；**无条件阻止重放/重规划派发**，只能原可信恢复流程处理。
- `observed_failure`：Receipt `ok=false` 已提交，但**不是已终结失败证明**；需要锁行、校验 owner、Turn、worker generation token、snapshot / args digest、tool version、Receipt 格式和当前未完成 step，方可升级为可信失败。
- `failure_finalized`（建议新的新格式专用终态，名字为合同）：只在同一 DB 事务中写入绑定的 `AgentAttempt(status=failed, error_code, bounded projection)`，维持 step=pending、原 Receipt immutable、append-only `AgentStateTransition`（如 failure_observed）后写入；失败不能创建 `executions[step_id]`，不得把 `AgentTaskState` 标 completed。

### 2.2 真正不能自动重试的状态
- `observed`（工具成功 Receipt 但尚未 checkpoint）依然是**未解决**：不能因为 Receipt 成功就自动重跑或跳过 checkpoint。
- `dispatch_unknown` 和不合法/截断/来源不一致的 `observed_failure` 是 UNKNOWN/UNVERIFIED：没有用户授权也不能复用其他看似合理结果。
- `failure_finalized` 只是“工具明确报告失败”，**不自动代表 retryable**。Host 必须根据可信工具契约及失败类别给出重试准入；通常未证明时直接修订或澄清。

### 2.3 避免污染 legacy
保留 legacy M4 的 `UNRESOLVED_DISPATCH_STATUSES` 语义；专门给新格式写 `verify_and_finalize_dependency_failure_in_session`。正常 `record_receipt` 仍先产生 `observed_failure`，随后在受信事务做最终失败投影；有 Receipt、无终结时不能开放执行。

**审查修正：** 原先将 `observed_failure` 与缺 Receipt 混成统一“结果未知”不准确。应分别返回 `verified_failure_pending_replan`、`reconciliation_required` 等安全码，但必须从 DB 状态得出，不能仅靠 adapter 返回字典。

## 3. Revision 与证据继承：冻结的最小快照模型

### 3.1 兼容策略
- **创建模型不修改：** `DependencyPlanCreation(revision=1, all pending)` 继续只接初始创建。
- **原 S25-06 历史 runtime snapshot 不改写：** `dependency-plan-v1 / dependency-plan-read-v1` 依旧单独只读验证，`revision==1` 仍有效。
- **新增显式的 revised runtime 版本判别**（建议 `dependency-plan-read-replan-v1`），旧计划在“首次受信修订成功”时才原子迁移到修订 runtime 判别，而不是把旧创建 parser 改成接受任意 revision。此处判别与版本号是 Step 4 需检查所有调用方的兼容接口；候选字符串不表示已新增工具授权。
- 修订后 `plan_revision := old + 1`；历史成功 execution record 的 `plan_revision` **永久保持产生时的 origin revision**，不能更新成新 revision。新增保存的 lineage 需绑定 `parent_snapshot_sha256`、`revision`、`preserved_step_ids`、每个保留 step 的 `dispatch_id`、源 tool version/arguments digest，并验证 parent→child 连续性。为审计防伪，lineage 必须源自锁定的 DB parent snapshot / immutable evidence，不接受模型自填。
- **证据保留白名单**：完成前缀的 step id/goal/depends_on/expected_tool/tool_version/argument_bindings 完全不变，且每个 dispatch、receipt、attempt、source refs 重新符合 Host 校验；只有未完成后缀允许变更。改变已完成 upstream input 后试图沿用其输出必须拒绝。
- **顺序图限制**：仍为 2–8 个按 id 上升的有序依赖步骤、已完成连续前缀、后缀 pending；暂不实现并行执行或完成步骤失效后在原计划内重新运行。
- 需要一个**严格独立的 runtime validator**和共同 evidence verifier；不要在 `_step_for_read` 内用 `if revision >=1` 直接放松旧版本所有条件。

### 3.2 最小修订事务
输入：`owner_id, conversation_id, turn_id, expected_raw, expected_revision, trusted_replan_proposal, parent_evidence_refs`。
前置：当前用户 Turn 经 Primary / Host 准入，owner/route 有效；**没有其他正在执行、未释放的 Claim/active worker**；所有 unresolved dispatch 已核查安全；候选只更改 pending 后缀；有效权限仍限原 read-only 两工具；原始源 quote/Observation 字段符合既有绑定合同。
事务内按既有锁顺序：`ConversationPlan FOR UPDATE → PlanExecutionClaim FOR UPDATE / assert absent → (Fence 若存在则须无 active foreign worker) → Dispatch/Attempt evidence → AgentTaskState`，然后核对当前 `ConversationPlan.snapshot == expected_raw`、old revision、owner、tool registry/version、source lineage；CAS 写入新 snapshot 与 TaskState projection，追加 revision transition，最后绑定 Turn 响应。只要任何一步冲突，整体 rollback，不可部分提交新计划或“假成功”答复。
事务结束后**不自动 dispatch**；新 RESUME Turn 必须从 Primary 重新准入，并获得自己的 Claim / Fence / preturn digest。

### 3.3 “用户目标变化”不等于“失败驱动”
当前用户直接改变约束可由原 Primary 解读并提议 pending 修订，但不把外部工具文本当用户 goal。若目标变化否定已经完成前缀的有效性，停止自动继承，给出需要重新建计划/澄清的安全行为；不能靠模型自评宣告等价。

## 4. 重试 DB 方案：先锁合同，不在首切片启用

**不能使用新 step_id 伪装同一步重试，也不能只把 `observed_failure` 从全局 unresolved tuple 去掉。**

### 4.1 真正的多 Attempt 支持（后续受控切片）
- 新格式 `PlanToolDispatch` 加 `attempt_ordinal INTEGER NOT NULL DEFAULT 1`，对应的 Alembic migration 作为单独改动，替换原 `UNIQUE(turn_id,step_id)` 为 `UNIQUE(turn_id,step_id,attempt_ordinal)`。保留 `dispatch.id` 唯一值为 `AgentAttempt.id` 和历史 source ref，legacy 仍仅允许 ordinal=1 / 同 Turn 单次派发。
- 对 PG 用原子锁与 DB 唯一性保证 ordinal 单调；历史旧行 backfill=1。SQLite 需采用 Alembic batch alter/recreate，验证 SQLAlchemy metadata 与 migration 实际一致，迁移前数据库备份。严禁只修改 model 不提供迁移。
- 同一会话同 step 的新 Dispatch 准入须检查：上一相关 attempt **可信 failure_finalized**、新的 Host permit 和 claim/fence、无该 step 的成功 checkpoint、没有任何 unresolved/unknown、重试预算跨 Turn 持久有效、不会违反新计划 source binding。
- 现有 `services/dependency_recovery.py` 假设同 Turn 每 step 至多一 Dispatch、并限定 dispatch 状态集合；在增加新状态/ordinal 前必须同时升级其只读检查逻辑和已有 Step5 PG 测试，**但不得更改其 operator-only 权限**。
- **重要简化建议**：首版先仅支持“修订 pending 后缀中的未派发 step”；若目标失败 step 已有 `failure_finalized`，只有上述 attempt 支持完成后才可以在相同 step_id 上重派发。不能在没有多 Attempt 时宣称 R02/R03 的失败后继续链路已经完整。如果希望先实现失败后替换步骤，可采用显式 supersede 失败 step、分配未用过的新 step_id 并更新后续依赖，且旧失败 step 仍可审计；**这属于结构性改计划而非伪装重试**，必须在 Step 4 前额外明确验证顺序、2–8 上限、source refs 和模型用户体验。默认不采用这个绕行方案。

## 5. Primary/Host 与人工参与的授权判定

- **统一 Primary 不变**：用户本轮请求→ Primary 单一语义 route→ Host admission（`PrimaryRouteEvidence` 不可变），现有 `apps/api/main.py`。
- 已拥有合法的 FAILED Receipt 后，可以用**同一 Primary 组件**基于 Host 构造的可信失败事实提出 `ReplanProposal`，但这只是从属建议，不能变成另一套独立的 routing admission。
- **FIRST/RESUME 原授权不能默认为新执行授权**：不修改已记录的 `PrimaryRouteEvidence.route_kind`、`preturn_snapshot_digest`；不能在新 revision 上复用旧 token。若要当前 Turn 自动再次派发，需要独立证明原范围与新的 dispatch 签名一致；首次实现采取：失败后给出受信分类、生成候选 / 安全停止；下一用户 Turn 对修订进行 Primary/Host 授权，CAS 保存计划，下一独立 Turn 才按新快照 RESUME。
- 自动性不能被清空：S25-07 的模型必须*基于真实失败原因*给出合适的局部候选并可一次性保存，无需用户手工逐字编辑整个计划；用户授权是执行权限边界，不是把重规划完全交给用户。
- 不增设新的 HTTP 直接执行端点，不开放 operator reconciliation。用户体验允许“失败→推荐且保存可执行修订→下一轮合法继续”，但在不确定失败时必须停并说明原因。

## 6. 真实验收 Gate 与分段交付

### Gate A — 可信失败终结
- 真实 Registry adapter `ok=false` → bounded Receipt → failure_finalized + failed AgentAttempt + append-only transition；step 保持 pending，0 第二次 dispatch。缺 Receipt、篡改 tool_version/args、外来用户、迟到 receipt/worker fence 的负向检查必须拒绝。
- API 不将 verified_failure 文案混为 unknown；同时仍保证未知不重放。SQLite/PG 同样验证。

### Gate B — 版本化原子后缀修订
- 完成第 1 步+失败或无派发的后继 → Primary 提议 → Host validate → revision=2/CAS、真实工具/参数展示、旧 completed/Receipt/Attempt 源 ID 不变；旧快照仍 revision=1 原合同运行。
- 跨版本引用 `definition` 继续成功，若把 parent term/arguments/source dispatch 替换则拒绝；DB TaskState projection 只增版本不假造 step completion。
- 并发两个修订提交必须 1 winner/1 conflict；与 resume 竞争时绝不双发或旧 worker 越过新 snapshot。

### Gate C — 新 Attempt / 有界安全重试（不与 Gate A/B 强捆）
- 用受控但真实经 Registry 调用的 transient read failure，证明允许重试的明确失败类；失败不可重试时 0 第二 dispatch；成功后只保留 1 个可信 completed source，但历史失败可查。
- PG/SQLite migration、legacy M4、recovery operator 假设和同 Turn unique 全回归；超过预算 fail closed。真 Qwen 不少于 Step 2 规定挑战，按数据库结果评分。
- **禁止**为了绿色测试将任意 `ok=false` 视为可重试。

### Gate D — 正式 API 和模型挑战
正式端点 `POST /conversations/{cid}/agent/messages`；真实 PG multi-connection/process conflict & crash；真实 Qwen 多种自然语言任务与失败/约束变更；统计含错误类型、实际 dispatch/attempt、checkpoint/evidence、model version、flags、unknown 处理。S25-06 E3/PG/M04、S25-03/04/05/Primary 全回归。

## 7. 设计阶段安全审查与剩余前提

**P0 已冻结的安全原则**：可信失败不等于未知；所有重派发有新授权及持久预算；历史成功证据不可改写；修订必须在无外部活跃执行 Claim 的安全点做 CAS；用户语义永远不赋予 Host 权限；旧格式不迁移。

**剩余实施前选择，不能视作已验证**：
1. revised runtime 的精确 schema/version + parent lineage 验证函数签名及每个旧 Host caller 的兼容矩阵；
2. failure_finalized 状态是否复用旧 `PlanToolDispatch.status` 还是单独带版本的 evidence 记录，以及对应 operator-only inspection 如何保持 fail closed；
3. Gate C 的 Alembic 迁移是否必须在 Gate B 之前落地；若失败步骤不可 re-dispatch，则不能宣布 Gate B 已经达成完整“失败后继续”；
4. Primary 是否可在当前 user request 中给出合法后缀修订/参数来源，还是须单独下一 Turn；需要以真实 Qwen 的行为合同审查；
5. 真正可触发的 retryable 只读失败是否存在，没有则按受限功能范围验收，或事先引入真实可信测试工具 adapter（不能 fake 成功）。

**结论：这是一份具体可执行的集中合同收口底稿，但 Gate A/B/C 的实码接线与 DB 迁移仍待 Step 4；本次没有把 S25-07 标记为 CLOSED。**

**下一阶段建议：** 审阅本合同后进入 Step 4 首切片 Gate A（可信失败终结与 HTTP 真实状态），并在启动前针对以上剩余选择做一次代码级 preflight；不得在未完成门禁时跨越到自动重试。未经授权不修改运行代码、不合并 PR #49。
