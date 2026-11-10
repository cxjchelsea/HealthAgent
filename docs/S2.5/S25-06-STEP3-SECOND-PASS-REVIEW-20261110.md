# S25-06 Step 3 v2 — 二次设计复审（Implementation-gated approval）

- 日期：2026-11-10
- 代码基线：main@9e2e79a473089be47d25d1c10b7a9fa82f06d89b
- 文档基线：PR #35 的 `S25-06-STEP3-TECHNICAL-DESIGN-20261110.md` v2（提交 631fed8；归档提交 b2eb1ea）
- 方法：重新阅读现有 Host/M4/PlanStore/Dispatch/Evidence/TaskState/版本相关真实代码，逐一对照首次专项复审 F1–F6；无本地 pytest、PostgreSQL、真实模型物理执行。
- 裁决：**CONDITIONAL DESIGN PASS**。此前 5 个 P1 的矛盾已给出一致的目标行为和拟议 API；允许以此制定 Step 4 的最小实现切片，但**不代表新实现已通过验收、不开默认开关、不授权直接合并**。

## A. 逐项复审

| 项目 | v2 处理 | 本轮结论 |
| --- | --- | --- |
| F1：同 Turn 多步骤依赖上游已 checkpointed 证据 | v2 §4：每完成一 Step 在同一事务执行 Plan CAS + Claim.snapshot 滚动 + Dispatch checkpointed + Attempt/TaskState；commit 后更新 current_raw，才读取下游受信结果 | **设计通过，实施 P0 门禁** |
| F2：text_stats 不支持旧 saved plan 路径 | v2 §2/§5：新格式 `dependency-plan-read-v1` 协议，`mcp_reference_lookup@1.0.0` 与 `text_stats@1.0.0` 固定 adapters、新计划专属 Host grant，旧 PlanReadInvoker 不变 | **设计通过，实施 P0 门禁** |
| F3：模板/具体参数及旧 checkpoint 比对冲突 | v2 §2/§4/§6：不可变 argument_bindings + execution record + persisted concrete args；唯一 step_id/dispatch_id/digest/来源关联；新比较器与旧版隔离 | **设计通过，实施 P0 门禁** |
| F4：结果截断和来源不可信 | v2 §6：只引用完整、允许的 `definition` 字符串；截断/缺失/越权/类型错误 fail closed | **设计通过，实施 P0 门禁** |
| F5：API 所有计划操作必须隔离 | v2 §7：create/first/resume/revise/cancel/read/recovery/finalize/rollback 矩阵，关闭时仅安全只读、零 Dispatch；Legacy 原链保持 | **设计通过，实施 P0 门禁** |
| F6：物理证据与复杂度 | v2 §8–§10：崩溃切点、双 Worker、Qwen 真模型、flag off | **列入 Step 4/5 验收，尚未执行** |

## B. 代码交叉检查与新增注意事项

**B1：Claim 滚动必须防止双重提交。** 现有 `ConversationPlanStore.compare_and_swap_in_session` 检查 Claim 与 expected_raw，`acquire_execution_claim_in_session` 保留初始 snapshot。新 `advance_claimed_snapshot_in_session` 必须锁 Plan 行、检查 Claim 是同一 Turn、Turn running、expected_raw 严格相等，再把 Plan 和 Claim 的 snapshot 在同一事务滚动。提交成功后单一 Host 状态更新 `current_raw`；异常不能用旧 raw 继续 Dispatch。需要 PostgreSQL 并发 CAS 物理证明，SQLite 通过不足够。

**B2：避免重复投影。** 旧 `project_finalized_turn` 对已存在 AgentAttempt 主动抛 `s25_projection_duplicate_dispatch`，而新版 per-step 已写入 Attempt。因此新格式终结事务只能写终态、剩余必要的状态汇总与 Claim release，不能二次投影相同 Dispatch；增量投影必须「完全相同记录幂等、不同绑定拒绝」。`AgentTaskStateStore.save_in_session` 当前也依赖 `step.expected_arguments` 的 digest，需要新格式严格分支；不可用旧 projection 解释 argument_bindings。

**B3：M4 Journal 不替代授权。** `PlanDispatchJournal.begin` 检查 Turn running 与未决同 step Dispatch，但自身不会检查 PlanExecutionClaim 所属 owner；所以新 Runner 每次实际 Dispatch 仍必须执行同轮 Host plan grant、latest Claim + snapshot 的 Guard 验证，严禁只调用 Journal.begin。`checkpoint_step_in_session` 必须确认 dispatch_id 和 Step、工具版本及具体 arguments 的完整绑定。

**B4：来源完整性。** `bounded_projection` 对超过 1200 字符的字段标记 `definition_availability=truncated`，并不保存数据。新结果解析器只能引用 checkpointed 的 M4 receipt 且 `definition` 实际存在、短文本完整、目标 `TextStatsArgs` 严格校验通过；不能回退到 legacy `value`，不能用模型输出重新构造。

**B5：版本及 route 无后门。** 旧 `plan_versions_compatible` 强制 `PlanLedger.restore` 且 legacy executor version；新计划只在新格式 Host admission/feature flag 下进入新版 adapter。普通 Registry `READ_REGISTRY.invoke` 虽支持显式版本绑定，但 ordinary grants 不可用于 saved-plan；需要独立的 plan-specific grant 与 pinned exposure。所有入口必须覆盖，含先于 AgentLoop 的 early-return。

**B6：崩溃后历史成功不能静默重演。** 当前旧 Claim 可以在 owner Turn terminal 时清除，但 06 必须在清除/换新 Claim 前查未决 Dispatch；对上一个 Turn 已 checkpointed 的前缀不再派发，同一 Step 还存在 unknown/observed 时必须人工或安全对账。不能宣称外部 exactly-once。

## C. 实施范围的明确建议

**先实施 Step 4 Slice A，限定为纯 Schema/DAG/版本解析与负例单元测试。** 不在 Slice A 写入任何生产计划、不更改正式 HTTP 路由、不扩大工具授权、不引入数据库迁移；只开发默认不接主链的 `dependency_plan_schema.py` + tests，禁止借 Slice A 宣称已完成 06。

建议的退出标准：
1. strict schema，2–8 steps，稳定 step id，正确区分老/新/未知格式；
2. 无环、依赖存在、依赖在前、观测引用与 declared dependency/工具输出字段一致；
3. literal source quote 与请求绑定，不匹配拒绝；
4. 计划创建阶段无实际工具执行、零 Dispatch；
5. pytest + 原 legacy PlanLedger/版本/Host 测试无回归；
6. 实际代码审查，确认新 Schema 不能在绕过 Host 时被误用为执行许可。

之后按 v2 文档 Slice B→C→D→E，**Slice D 不能开始直到 B/C 在版本与来源链上物理验证**。Slice D 必须有 PostgreSQL 逐步提交、双 Worker/崩溃恢复和旧投影兼容测试。

## D. 最终裁决

- Step 3 详细设计：**有条件通过**，上轮 F1–F5 的设计阻塞被具体方案覆盖。
- Step 4：**可准备 Slice A 最小实现**；本复审本身不代表自动授予生产改动权限。按既有用户授权习惯，实施前需明确授权。
- Step 5：真实模型与 PostgreSQL 证据尚无，不可声称 PASS。
- PR #35 继续 Draft，未经用户明确许可不合并。
