# WP-C Phase 4-E 首次未见挑战原始失败归档（2026-11-04）

## 不可追认的首次结果

测试脚本首次冻结在 `246d7556e941ecb9eb3d5fe8c43244625a29d318`；`manifest_sha256=7214d77d2c0d52175ecf0b7571bc18392d8210439c2c497f541d0e3bb20ade2d`。用户通过真实 `ollama_proxy/qwen3:14b`、Unified、一次独立 SQLite 数据库执行后上传原始 `phase4-e-unseen-v1.json`，运行时间 UTC 2026-11-04T03:17:51Z。原始文件在用户对话附件中，**未写入 Git 仓库**。

**首轮不可篡改裁决：14 PASS / 3 FAIL / 2 BLOCKED；总 FAIL。** 不能通过修改评分器将首次结果回填为 PASS。以后的所有重跑都是已知回归，不得再叫作首轮未见样本。

## 失败根因与风险分类

1. `PLAN_FIRST_A`：HTTP `pending_plan_steps`、Primary `plan_execute_first/allow`、calculate 成功，计划状态 `completed/pending`。但 SQLite `attempt_count=1`，评测器原先对所有 PLAN 场景一概预期 Attempt 0，导致 `physical.effect_ok=false`。这是**验收器契约误判**，不是 FIRST 执行失败。必须将计划执行回执计入预期，同时查验其 tool/status，不能直接忽略 DB Attempt。
2. `PLAN_REVISE_B`：Primary `plan_revise/allow`，Host `primary_v1_revision_candidate`，结果却是 `plan_revision_conflict`，Plan 未更新。现有 `apply_plan_revision` 对非目标步骤后缀限定 `不变/原样/无需修改/不修改`，不接受用户的“第一步不要动”。属于**步骤保留约束绑定过窄**；应只修正明确否定的非目标步骤标记，不改变单一主语义路由或放宽旧表达式严格校验。后续 `PLAN_CANCEL_B` 被阻断。
3. `PENDING_CANCEL_REPLY`：Primary `respond/allow`，0 Task、0 Attempt，但 `PendingWriteAuthority.status=pending`，未满足 durable revoke。原因是 `explicitly_withdraws_pending_task_write` 的窄模式没有覆盖“取消刚才那条待办的创建请求，别替我新增任务”；模型选择非消费类 respond 时也未收尾旧授权。属于**P0 悬挂授权**，必须通过 Host/Turn 事务内的状态治理终结授权；仅增加同义词匹配不能视为完整安全方案。

## 已通过且必须保留的行为

14 个 PASS 包括计划创建、普通任务与重复请求 409、记忆权限拒绝和允许、Pending Task 续答及重放、Pending MCP 只读、无保存计划的限制。已有业务 Turn `semantic_attempts=1`、Primary Evidence 逐 Turn 对齐。针对失败的修复必须证明这些成功路径未退化。

## 整改与再验收门禁

1. 首次 v1 报告不动；另有明确版本的回归运行和新的精确 HEAD。
2. FIRST 修复评分器的物理 Attempt 计数并增加 succeeded calculate 对应检查；不放松 FIRST 只执行一步。
3. REVISE 补充非目标步骤“不要动”保留语法的针对性测试；旧值、目标步骤、CAS、工具类型仍严格绑定。
4. PENDING 撤销补充不依赖仅关键词的终态治理：对已绑定的 Pending 任务授权，若本轮 Primary 选择非续写路线并且没有执行消费，应在 Turn 终结事务内撤销；显式撤销始终否决写入。保持同一 DB CAS，冲突不得假装成功；补充 SQLite HTTP 实际撤销和 PostgreSQL 撤销/消费并发回归。
5. 仅在新 HEAD 的全量 CI、定向回归与真实 Qwen 回归通过后更新阶段裁决。AC2 函数级全路径审查仍未结束，Phase 4-E 总体 HOLD、禁止合并 main。
