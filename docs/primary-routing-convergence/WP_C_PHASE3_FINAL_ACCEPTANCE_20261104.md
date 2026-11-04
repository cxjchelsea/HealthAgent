# WP-C Phase 3 — 真实模型完整验收归档（2026-11-04）

## 精确结论

**既定 Phase 3 完整业务矩阵 PASS：14/14，FAIL=0，BLOCKED=0**。本次依据用户本机执行 `python -m evals.primary_wp_c_phase3_live` 后提供的原始 `phase3-final.json`。原始 JSON 是对话附件；仓库内本文件为安全摘要，**不能冒充原始物理运行文件已入库**。

- 被测代码精确 HEAD：`5224ae2823347b5ef8b2998de861e91c9897c561`
- 仓库/分支：`cxjchelsea/HealthAgent` / `refactor/primary-routing-convergence`
- 报告 generated UTC：`2026-11-04T01:09:16.421823+00:00`
- API：本机 `http://127.0.0.1:8012`，`ollama_proxy` / `qwen3:14b`，Unified Primary v1
- 测试入口：`evals/primary_wp_c_phase3_live.py`，合成任务与记忆，隔离数据库由操作者负责
- 同一精确 HEAD 的 GitHub Actions：run `41751894071`，conclusion=success（CI 并非真实 Qwen 物理验收）

## 14 项场景与证据

| 场景 | 结果 | 观察 |
| --- | --- | --- |
| preflight | PASS | ollama_proxy、qwen3:14b、unified、primary_active=true |
| PLAN_SETUP | PASS | plan_create/allow；plan_created_pending_execution；两步 pending，0 工具 |
| ORDINARY_WITH_PLAN | PASS | ordinary_tool/allow；calculate 成功，status=completed/model_final；保存计划仍 pending/pending |
| MISSING_REFERENCE | PASS | plan_missing/restrict，primary_v1_missing_plan_status；0 工具 |
| TASK_WRITE | PASS | ordinary_tool/allow；primary_v1_task_created，成功 create_task 一次 |
| TASK_REPLAY | PASS | HTTP 409、0 模型/工具 |
| MEMORY_DENY | PASS | ordinary_tool/deny，primary_v1_memory_consent_required；0 写入工具 |
| MEMORY_ALLOW | PASS | ordinary_tool/allow，primary_v1_memory_saved，成功 save_memory 一次 |
| PENDING_TASK_ORIGIN | PASS | clarify/clarify，needs_input；0 工具 |
| PENDING_TASK_ANSWER | PASS | clarify_response/allow，primary_v1_pending_task_authorized，pending_task_created；create_task 成功一次 |
| PENDING_TASK_REPLAY | PASS | HTTP 409、0 模型/工具 |
| PENDING_MCP_ORIGIN | PASS | clarify/clarify，needs_input；0 工具 |
| PENDING_MCP_ANSWER | PASS | clarify_response/allow，primary_v1_pending_read_only，verified_clarification_execution；mcp_reference_lookup 成功一次 |
| PENDING_MCP_REPLAY | PASS | HTTP 409、0 模型/工具 |

所有非 REPLAY 业务请求报告 `categories.primary=1`、`evidence.semantic_attempts=1`，对应同一个 Primary route schema 与 Host disposition。普通 AgentLoop 的独立计算 `model_http_attempts=3` 包括路由后的决策，不代表三次独立的主语义所有者。每个 REPLAY 的 `model_http_attempts=0`。

## 通过范围与未完成门禁

这次通过**证明在所测固定请求集上**单次主语义提案、Host 准入、业务成功与拒绝质量、重放行为符合 Phase 3 验收器；不声称任意语言变体都稳定、跨进程持久化无问题或所有权限组合已穷举。报告尚未独立物理回读 SQLite/PostgreSQL 的 Plan、Turn、Attempt、PendingWriteAuthority 记录；也未包含高并发、硬崩溃、未知工具结果或生产授权。

此前 v1–v4 定向失败对应 Primary JSON 严格字段生成与普通计算生命周期隔离，整改均保留 Runtime schema fail-closed 与原始 Host 权限。v5 Pending 两条链路 7/7 PASS 后，本轮完整 14/14 PASS。

后续：
1. **陌生挑战集**：独立于本轮使用过的提示词样本，至少覆盖 Pending 撤销/改话题/无效短答、创建写入不可授权、同会话重复写、MCP 失败、语义歧义与保存计划状态冲突。不可只重跑调参句证明泛化。
2. **WP-C Phase 4**：数据库独立回读与持久证据、并发/跨进程崩溃/Prepared unknown/replay、跨入口写入锁与权限阻断；已存在的 PostgreSQL CI 专项可复用，但必须区分运行 HEAD、被测范围和缺口。不要把 CI 通过一概称为真实 Qwen 物理验证。
3. **AC1–AC5 最终复审与冻结合同核对**、与 main 基线差异、PR 精确 HEAD、分支保护及 CI；最终合并须用户明确授权并用 Standard Merge Commit。

**裁决：Phase 3 已定义的完整业务矩阵 PASS；扩展挑战与 Phase 4/AC5 仍待验收；禁止直接合并 main。**
