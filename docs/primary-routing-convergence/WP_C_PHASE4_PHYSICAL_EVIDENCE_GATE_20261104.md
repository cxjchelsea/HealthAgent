# WP-C Phase 4 — 真实模型执行证据与数据库故障门禁（冻结实施底稿，2026-11-04）

## 基线及优先次序

业务代码基线 `84993426f28a7431ad2ae9a36944c0a21490f378`（仅整改了既有评分器的中文数字等价检查）；独立挑战 v3 在该 HEAD 上 12/12 PASS。此后只追加验收文档，没有执行器行为变更。WP-C Phase 3 业务矩阵 14/14 PASS。

**Phase 4 尚未物理验收，禁止标记 PASS**。验收不是把以往 S25-03/M4 的 PostgreSQL CI 结果复制为新的独立实验；需要为当前分支建立可查询、可复验的 Turn、PrimaryRouteEvidence、PendingWriteAuthority、AgentAttempt、ConversationTask 证据链接。

## 检查顺序

1. **P4-A 真实 Qwen + 隔离数据库独立回读**：在同一测试数据库下先运行 Pending Task 成功、明确撤销和 MCP 补参三条链；随后用独立只读 DB Session 检查 `turns`、`primary_route_evidence`、`pending_write_authorities`、`conversation_tasks`、`agent_attempts`。每个业务 Turn 仅一条 `primary_route_evidence`，`semantic_attempts=1`，`preturn_snapshot_digest` 为 64 位摘要，`disposition` 与 HTTP Host 决策一致；记录 `route_kind`、`admission_reason`。仅成功 Task 回复允许一条 `ConversationTask` 和一次 `create_task/succeeded` Attempt；撤销请求必须为零任务与零写入 Attempt；重放不得新增记录。
2. **P4-B 持久授权生命周期**：读取第一次澄清时签发的 `PendingWriteAuthority`，验证授权属于同一会话/原始 Turn，并以唯一 `consumed_turn_id` 与完成的 Task Turn 关联；撤销场景不得 `consumed`，不会误写成功回执。特别核查撤销后授权记录的 status 可能仍为 `pending`，但由于最近 Turn 已非待补参请求而不能从正常 API 恢复；这不等于授权已被物理撤销。若产品需要强销毁授权，另行设计显式 CAS 与审计事件。
3. **P4-C PostgreSQL 双会话/双工作进程竞争**：复用 `tests/test_primary_pending_write_postgres.py`、`tests/test_primary_ac4_cross_entry_postgres.py` 以及 M4 claim/dispatch 专项，在同一精确 HEAD 证明两次并发消费至多一次写入，普通写入与 Pending 不可跨入口绕开共享锁；记录实际数据库引擎、隔离级别、进程数与错误码。
4. **P4-D 崩溃、prepared、unknown outcome**：复用已经存在的 M4/S25-03 物理工作流及新 Primary 路由 Journal，检查 prepared 未完成、未知结果、请求重放及 API 重启后是否按合同安全拒绝或人工核对，绝不把未知结果写成 succeeded，也不得在没有真实外部 receipt 证明时自动重发。
5. **P4-E AC1–AC5 终审**：综合两组真实模型业务报告、独立语句失败修复证据、全量 CI、每 Turn durable Primary 证据、13 个 Legacy Reviewer 0 调用、权限与只读/写入混合入口、主链差异、默认路由开关。最后核对 PR/精确 HEAD/分支保护，用户明确授权后才能 Standard Merge Commit。

## 物理 PASS 判定

- API 成功或失败的业务结果须和数据库行内容一致，不能只看响应 trace。
- 一切已执行工具都有先于效果写入的可信准入与 Dispatch 证据；拒绝、撤销、重放不出现被允许之外的 succeeded 工具效果。
- PostgreSQL 并发和断电式中断可以通过受控故障注入或已完成的精确 HEAD CI 专项证明；不得标记为“本机 Windows 上真实 Qwen 故障注入”，除非确实运行。
- 敏感标题、回复与用户消息不能进入公开验收 JSON，仅保留散列、计数、状态、元数据和 UUID；原始数据留隔离测试 DB。
- 每个失败明确分类为产品行为、模型语义不可靠、测试夹具误判、执行器/证据治理缺陷；必须保留原始 FAIL 历史。
- Phase 4 的 PASS 不能自动代表医学场景可部署或高风险动作无需二次确认。

## 当前证据缺口

现有 `evals/primary_wp_c_independent_challenges.py` 没有在脱敏报告中保存各组的 conversation_id / turn_id，因此单凭已上传的 v3 JSON **不能**完成数据库逐 Turn 的独立回读绑定。接下来应添加专用 Phase 4 物理测试驱动，在新数据库创建隔离会话并持久保留 ID，或者由脚本在受控数据库中通过 request_id 唯一定位；不要猜测 ID 或把先前的私有测试数据库当作同一运行证据。

AC4/AC5 当前 **HOLD**；本文件为下一工作包执行合同，不替代实际物理运行。
