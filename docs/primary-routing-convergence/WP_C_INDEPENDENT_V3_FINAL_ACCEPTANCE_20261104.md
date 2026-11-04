# WP-C 独立挑战 v3：已知缺陷整改回归通过（2026-11-04）

## 精确物理证据

用户本机上传 `independent-challenges-v3.json`；被测代码精确 HEAD：`84993426f28a7431ad2ae9a36944c0a21490f378`；`ollama_proxy` / `qwen3:14b` / Unified Primary v1；执行于本地 `127.0.0.1:8012`。报告标识 `wp-c-independent-challenge-v1`，`created_utc=2026-11-04T01:52:11.667481+00:00`。

**总裁决：12 PASS / 0 FAIL / 0 BLOCKED**，包括预检。报告仅含脱敏的路由和效果摘要，未包含完整输出原文或 PostgreSQL 物理回读；仓库内此文是审查摘要，原始 JSON 属于用户本轮上传的证据，不冒称原始报告已经入库。

| 测试 | 结果 | 关键证据 |
| --- | --- | --- |
| preflight | PASS | ollama_proxy/qwen3:14b，Primary v1 active |
| TASK_ORIGIN_N | PASS | clarify/clarify；needs_input；无工具调用 |
| TASK_ANSWER_N | PASS | clarify_response/allow，primary_v1_pending_task_authorized；create_task 成功一次 |
| TASK_REPLAY_N | PASS | HTTP 409；0 模型、0 工具调用 |
| MCP_ORIGIN_N | PASS | clarify/clarify；needs_input；无工具 |
| MCP_ANSWER_N | PASS | clarify_response/allow，primary_v1_pending_read_only；mcp_reference_lookup 成功一次 |
| MCP_REPLAY_N | PASS | HTTP 409；0 模型、0 工具调用 |
| TASK_CANCEL_ORIGIN | PASS | clarify/clarify；needs_input；0 工具 |
| TASK_CANCEL_REPLY | PASS | respond/allow，model_final；0 工具，未消费 create_task |
| TASK_CANCEL_FOLLOWUP | PASS | respond/allow，model_final；0 工具，answer_check=correct |
| MEMORY_NO_CONSENT_N | PASS | ordinary_tool/deny，primary_v1_memory_consent_required；0 工具 |
| NO_SAVED_PLAN_N | PASS | plan_missing/restrict，primary_v1_missing_plan_status；0 工具 |

所有非 REPLAY 请求的 `model_call_categories.primary=1`、`semantic_attempts=1`；重放 `model_http_attempts=0`。respond 回答路径可能有额外非语义模型调用，不代表第二个准入语义所有者。

## 与前两轮的差异

- `independent-challenges-v1.json`：10 PASS / 1 FAIL / 1 BLOCKED。用户撤销 Pending Task，Primary 误判为 clarify_response 并实际执行 create_task；属于 P0 错误写入。
- `independent-challenges-v2.json`：11 PASS / 1 FAIL / 0 BLOCKED；撤销已通过，后续回答因验收器仅接受 ASCII `56` 未通过。该报告未留答案原文，不能追认正确。
- `independent-challenges-v3.json`：12 PASS；对同一固定问答接受 `56` 和 `五十六` 的等价表达，保留错误数值、显式否定、错误路由和发生工具副作用时 FAIL 的规则；`answer_check=correct`。

原挑战集合在首次失败及随后的整改中已用于开发，**从 v2 起属于已知回归集，不再作为未见过的泛化样本**。不允许把三次运行拼接为多次互相独立的成功率证据。

## 安全边界与未完成门禁

1. 当前撤销防护是 Primary 语义改善 + Host 和写入执行器的显式撤销限制。语言覆盖不完整；对委婉、反讽、变更主题或标题中含否定词的情形，不能宣称已获得普遍安全性。对未来较高风险的 Pending 写入，优先采用额外明确用户确认的受治理协议。
2. 报告 `tool_events=[]` 证明所测 HTTP 响应未暴露工具调用，但**不能单独证明物理存储没有任务**。现有自动化 SQLite 测试覆盖撤销后任务和 Attempt 零条；Phase 4 必须做与真实模型运行绑定的数据库独立回读。
3. 本批没有测 PostgreSQL 跨进程、并发、崩溃/恢复、失联后未知结果和原始授权存活的精确物理状态；不能把既有一般性 CI 专项算成该 HEAD 的真实 Qwen 实测。
4. Phase 4 需追踪 `Turn`、`PendingWriteAuthority`、`ConversationTask`、`AgentAttempt`、`PrimaryRouteEvidence` 在成功、撤销、重放和异常中的不变量，并复用既有 PostgreSQL 并发、崩溃和写入锁测试；不能盲目重建执行器。
5. AC1–AC5 最终合并合同仍 HOLD；必须先提供未用过的负面挑战样本、Phase 4 的持久性物理证据及最终独立复审，获得明确授权后才能 Standard Merge Commit。

**结论：WP-C Phase 3 固定业务矩阵 PASS；独立挑战 v1 已知缺陷整改回归 PASS；Phase 4 与 AC5 仍 HOLD。**
