# S25-07 Step 3 — 独立设计复审与实现前门禁（2026-11-11）

**复审类型：静态独立挑战审查；非运行验收。** 源 main `e02845f7c3452ef452efceb20f7b725e5716529b`。审查对象：Step 1、Step 2、`S25-07-STEP3-TECHNICAL-DESIGN-20261111.md`，以及原始代码的 `dependency_plan_host/runner`、`dependency_dispatch/execution/observation`、`plan_dispatch`、`db`、`primary_host_admission_v1`、`apps/api/main`。

## 1. 综合裁决
**CONDITIONALLY SOUND / NOT READY FOR STEP 4**。设计沿用单一 Primary/Host、既有可信证据与只读范围，方向成立；但是以下 P0 问题若没有精确接口/事务合同，不允许直接编码。文档内的候选表/枚举/版本名仅是方案，不是正式冻结的可执行契约。

## 2. 独立发现及处理意见

### P0-01 — 现有同一步 retry 必然被双层拦住
- 代码事实：`PlanToolDispatch` 有 `UNIQUE(turn_id,step_id)`；`begin_dependency_dispatch_in_session` 又拒绝任何 `conversation_id + step_id` 历史派发；`UNRESOLVED_DISPATCH_STATUSES` 还包括 `observed_failure`。
- **挑战**：若只改 runner 或把 `observed_failure` 从全局 unresolved tuple 中移出，会破坏 legacy M4 或让未验证失败跨越 fence。尤其不能只允许跨 Turn 重试就绕过会话级 duplicate guard。
- **门禁**：提交 DB migration 决策（新 attempt ordinal / 独立 attempt identity），按新格式专用的 failure-finalized 终态解除阻断，旧 legacy 语义不变；测试旧数据和真 PG 唯一约束、重复写、并发争抢。

### P0-02 — 旧来源的 revision=1 不可直接变更
- 代码事实：`_step_for_read`、`dependency_observation`、`dependency_dispatch`、`dependency_execution` 多处验证 `source_record.plan_revision == current revision`。
- **挑战**：把所有校验改为 `<=` 并不足以防伪造 lineage；又不能复制旧 Attempt 并重标 revision。
- **门禁**：revision 链必须记录并可验证不可变旧证据、工具版本、arguments digest、依赖绑定及父快照；只允许未变 completed prefix，从父 revision 穿越到子 revision 的证据证明确实可信。显式反例：更换上游 term 后保留原 definition，必须拒绝。

### P0-03 — 当前失败 Attempt 缺失，工具失败状态与任务完成状态断裂
- 代码事实：`_project_step_in_session` 创建的 Attempt 是 `succeeded` 且发生在 success checkpoint；失败时 runner 在 Receipt 后返回 blocked，未投影对应失败 Attempt/TaskState。
- **门禁**：失败投影事务必须保持 step=pending，追加 failed Attempt/transition，保存 source dispatch/receipt 与失败原因；不能把失败标记为 completed，不能把有 receipt 的失败称为 unknown。

### P0-04 — 同一 Turn 二次模型调用不自动获得 Primary 权限
- 代码事实：`PrimaryRouteEvidence` 每 Turn 一行且不可变，`dependency_dispatch` 期待 route_kind `plan_execute_first/plan_resume` 并对 initial preturn digest 严查。
- **挑战**：新 `ReplanProposal` 是子提案，而不是新的正式 Primary；现有 Primary/Host 许可**未必**覆盖重新执行，特别是 FIRST 只授权首步。
- **门禁**：Step 4 第一切片应选择**失败后决策→原子保存修订→本 Turn 结束**，在下一 Turn 获得新的合法 RESUME 执行许可；后续自动重试仅当明确扩展且证明旧许可足够的 Host 合同（并重新测试 FIRST 与 RESUME 差异）。避免先实现“自动重试”再反推授权。

### P0-05 — 修订与活跃 Claim/worker 的安全点
- 代码事实：execution checkpoint 与 terminalizer 锁 `ConversationPlan → PlanExecutionClaim`，fence 在下游校验；`PlanExecutionClaim.snapshot` 随 step checkpoint 更新。
- **门禁**：计划修订必须保证旧 generation 不能在修订生效后继续派发/提交。不能在其他 worker 仍 active 时仅依靠 CAS 替换原 raw，也不能擅自调用 operator recovery。真 PG 竞态需要证明：仅一个新版本写入，另一个不会产生额外工具派发。

### P1-06 — 新 executor version 是一个候选，不是既定答案
- 仅新增 `dependency-plan-read-replan-v1` 字符串会触发当前 Host、schema、recovery 与 E3 精确版本门禁拒绝。**修正意见**：Step 4 实现前列出两种方案完整受影响的校验列表；首选“保留历史不可变 + 显式区分 revised runtime snapshot”，但具体是版本号升级还是 schema 判别字段必须依据兼容回归选择，不可提前宣称无迁移成本。

### P1-07 — 工具替代正向场景缺真实语义等价
- 当前仅 `mcp_reference_lookup` 与 `text_stats`，无法普遍互换。**建议**：保持 fail-closed 边界，R06 明确 `NO EQUIVALENT TOOL` 不视为失败；如要满足正式 S25-07 规定的替代动作，请后续在工具能力扩展的真实场景验证，不为测试制造伪等价。
- **禁止**将 S25-07 的有限范围误称“完整任意工具替代”。

### P1-08 — 目标语义忠实性的继承风险
- S25-06 最终审计留有 `1 strict xfailed`，意味着创建时 Host 不能普遍证明用户目标与计划等价。
- **门禁**：新的重规划必须以已持久化的实际工具/参数展示为准，不能用模型理由声称等价。与目标明显冲突需澄清/停止，不允许更高权限的执行；一般语义验证交 S25-08/18，保持 xfail 如实记录。

## 3. 优先级与 Step 4 启动前必须回答的 5 个问题
1. 如何将 `observed_failure` 经过不可伪造校验终结成允许**新尝试**的状态，同时原失败 Attempt append-only？
2. retry 的唯一性约束和多数据库迁移怎么做，如何不影响 legacy、当前 `UNRESOLVED_DISPATCH_STATUSES`？
3. 怎样证明跨 plan_revision 保留的 completed/source Observation 可信且没有被改写？
4. 已授权 FIRST/RESUME 的具体 scope 是否允许自动重规划/再次派发？若不能，首版只保存修订并下一 Turn 授权执行。
5. 修订对当前 Claim/Fence/Turn CAS 的安全事务时点在哪里？如何证明并发互斥？

## 4. 建议的下一阶段准入结论
- Step 3 **设计候选及复审记录已完成**，但仍需明确以上门禁与用户对限定范围的确认后，才能宣布 `READY TO IMPLEMENT`。
- 下一步推荐在本 PR 内做一份**Step 3 设计收口补充**，以“失败事实终结、revision lineage、原子修订”三个闭环给出精确状态转换/锁序/测试 oracle；不要直接 Step 4 大规模修改。
- 用户尚未授权任何 Merge；PR #49 保持 Draft，main 不变。

**复审结论：方向可行，但 P0 门禁尚未技术冻结；不得把本次静态复审描述为已实现或已验证。**
