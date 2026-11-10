# S25-06 Step 3 — 实现可行性专项复审（阻塞项）

审查日期：2026-11-10；基线：main@9e2e79a；对象：PR #35 的 Step 3 技术设计。
状态：CHANGES REQUIRED / NOT READY FOR STEP 4。本轮是代码交叉核对，未运行本地 PostgreSQL 或真实 Qwen，不能视为第三方独立批准。

## F1 / P1：同一 Turn 连续执行与 checkpointed 依赖矛盾

- agent/loop.py 的 saved-plan resume 在一个 Turn 内连续调用多步，逐步 observe 仅更新内存态。
- services/plan_dispatch.py 的 record_receipt 先将 Dispatch 标记 observed；checkpoint_in_session 才升级 checkpointed。
- apps/api/main.py 在 Turn 末尾事务统一完成 PlanStore CAS、checkpoint_in_session、S25-03 投影，而非在每步后完成。
- Step 3 要求下游读取上游已 checkpointed Receipt：若 A、B 位于同一 resume，则 A 尚未 checkpointed 时 B 不能执行。

**阻塞决策：** 建议保留同一 Turn 和原 Claim，在每一步完成后通过可信 step_id/dispatch_id/参数/Receipt 完成 per-step CAS + Dispatch checkpointed + 状态一致性提交，重新读取持久证据后才解析下游；不得在中间释放 Claim 或提前把 Turn 设为终态。必须设计最终化幂等性、重复投影安全性、partial crash 与整个 Turn 的累进 checkpoint。保守备选是每 Turn 仅执行一步，但与既定一次 resume 继续剩余步骤的行为不一致，需重新审定需求。不得继续用仅末尾提交的旧流程宣称中间依赖持久化完成。

## F2 / P1：候选动态目标工具并非旧计划可执行工具

- agent/tool_registry.py 中 text_stats 属于普通 read Registry，版本 1.0.0，参数 TextStatsArgs.text 最大 4000 字符。
- agent/tools.py:SCHEMAS、agent/plan_intent.py:READ_TOOLS、agent/plan_read_invoker.py 旧版路径不包含该工具；PlanReadInvoker 仍强制 legacy-plan-v1，调用旧 execute_tool。
- agent/primary_route_v1.py 的 ProposedStep 只允许 calculate/mcp_reference_lookup。
- 因此 mcp_reference_lookup.definition → text_stats.text 具备合理的字段类型配对，但**尚不能**走真实保存计划链路。

**阻塞决策：** 若以此为首个 E2E，需明确新 saved-plan adapter 与其独立版本、显式 Host plan-read grant、Primary 提案 Schema、M4 固定版本 Dispatch 和 feature flag；不允许直接把 Registry 1.0.0 赋给旧 plan executor，也不能用 mock 结果冒充正式主链验收。若暂不扩工具，必须证明另外一条既有工具的真实输出与目标参数类型匹配。

## F3 / P1：动态参数模板与实际 Dispatch 参数不能混同

- plan_invocation.inspect_plan_invocations 目前提前验证所有 pending 的具体参数并生成 digest，动态引用无法提前解析。
- plan_dispatch.checkpoint_in_session 强制 step.expected_arguments == dispatch.arguments_json；services/s25_03_projection.py 同样核对。
- 若只存 argument_bindings 模板并在执行时临时生成参数，旧 Checkpoint 比对会拒绝；若临时覆盖模板，则丢失绑定和源证据。

**阻塞决策：** 新 plan schema 明确不可变步骤模板 + 受信 resolved invocation 记录：step_id、dispatch_id、resolved arguments/digest、source evidence refs、tool_version、plan revision。新路径在 Host/M4 边界比较具体参数、模板和来源，旧路径维持原逻辑。不能继续沿用 PlanLedger.observe(name,args) 的歧义步骤匹配。

## F4 / P1：M4 Receipt 可引用字段必须证明完整性

- services/evidence_store.py 的 bounded_projection 在单字段 JSON 编码超过 1200 字符时只存 field_availability=truncated，不存原值。
- MCP 允许字段包括 definition/source/value，但旧 value 在 receipt 中还会转为长度最多 256 的字符串。
- 因此首次实现仅可允许完整存在、类型正确且长度有界的 definition，禁止静默回退 value 或从模型回答文本补数据。truncated/missing/不匹配返回稳定阻塞原因；需 owner、revision、版本、Dispatch/Attempt 严格核验。

## F5 / P1：新旧格式需覆盖所有 API 操作入口

- 旧 PlanLedger.restore、plan_versions_compatible、host_plan_binding_matches、inspect_plan_invocations 和 API 创建、执行、修订、取消、最终化路径均围绕旧快照。
- 仅在 AgentLoop 中按 plan_schema_version 分支不足以实现兼容。

**阻塞决策：** 给出计划操作入口矩阵：create/first/resume/revise/cancel/read/recover/finalize 的新旧/未知版本处理；旧格式保持不变，新格式 feature flag 默认关、未知及混拼格式拒绝执行，允许安全只读展示，禁止回退旧执行器。不得创建第二条语义主路由。

## F6 / P2：物理验证与成本门禁

需要确定性 DAG 测试、SQLite HTTP、PostgreSQL 双 Worker/Claim/CAS/崩溃测试；新增崩溃点覆盖 receipt 后 checkpoint 前、checkpoint 后下步前、下步 dispatch 后；重复相同工具和参数必须按 step_id/dispatch_id 去歧义。真实 Qwen 至少 2 类依赖任务 × 2 次，报告真实路由、派发、证据和失败分布。不得将现有 S25-05 的 26/26 成绩作为 S25-06 真实模型证明。

## 处置及门禁

1. 先完成 Step 3 修订稿：固定 per-step durable commit + 事务异常矩阵。
2. 固定可运行的 MCP definition → text_stats 闭环所需新 tool adapter / version / Host 授权，或选择另一条真正有效的工具对。
3. 固定模板与执行实例两层 schema、Observation 完整性与源关联、新旧 API 入口隔离。
4. 再进行 Step 3 二次复审，无阻塞 P1 后才允许 Step 4 实现。
5. 本次不修改生产代码、不合并 PR #35。

复审裁决：5 P1、1 P2；STEP 3 NOT ACCEPTED。