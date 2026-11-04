# WP-C Phase4-E — AC2 与 Pending 授权终态集中收口审查（2026-11-04）

## 基线与已知事实

基线为 `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`，独立审查在 `refactor/primary-routing-convergence` 进行；此文档不是合并许可。

- 初次未见 Qwen 挑战在 `246d7556`：14 PASS / 3 FAIL / 2 BLOCKED，始终保留原始 FAIL 历史。
- 修复后的已知回归在 `1d71be5b8dbf6d0896d031306626b5f73a92ef32`：Qwen3:14b 19/19 PASS；精确 HEAD CI `41774956482` SUCCESS。
- PostgreSQL 并发/竞争专项用户原始日志 `phase4-e-postgres-v2.log`：4/4 PASS。
- AC2 初次集中运行在 `1d71be5b`：25 PASS、17 FAIL；失败集中在 `auto` 路由解析继承开发环境配置导致 `primary_route_v1_active=False`，并非旧 Reviewer 被证明实际调用。
- 测试环境隔离在 `7d0f991b7755f78bc41c1afa79075b4fa77f8b5c`：三份配置测试显式 `_env_file=None`，CI `41778571486` SUCCESS，用户确认本地 AC2 复验通过。

## AC2 十三个旧 Reviewer 审查范围

函数名按照 `ACCEPTANCE_CONTRACT.md` 冻结，不因改名/包装改变口径：

1. review_plan_cancellation
2. propose_first_route
3. review_plan_intent
4. check_plan_edit_intent
5. classify_pending_reply
6. review_pending_reference_lookup
7. review_unsupported_capability
8. review_read_only_clarification
9. reconcile_execution_policy
10. review_plan_creation_reference
11. review_missing_plan_reference
12. review_plan_execution_authorization
13. review_plan_execution_scope

`tests/test_primary_ac2_all_reviewers_http.py` 对十三项设置 fail-on-call，覆盖双配置 CREATE/CANCEL/ordinary write/respond/reject/pending MCP；`tests/test_primary_wp_b_ac2_ac4_matrix.py` 对非写入、拒绝、无效提案、权限拒绝加双配置。 `tests/test_primary_wp_b_static_callgraph.py` 对 Primary 条件分支做 AST 检查。此次额外在 `tests/test_primary_unified_dual_profile_lifecycle.py` 和 `tests/test_primary_pending_cancel_http.py` 强制十三项 fail-on-call，将 CREATE→FIRST→RESUME、CREATE→REVISE→CANCEL、Pending 撤销/改话题/旧授权重放也纳入成功路径函数级保护，避免仅凭零调用而一律拒绝。

边界说明：AST 直接调用检测并非完整跨模块静态调用图；普通回答后置 AgentLoop 模型调用和 Primary 内 schema 重试不属于第二主语义所有者。全量 CI 与真实模型的调用分类计量共同提供独立补证。不能仅凭这部分测试称所有未来输入已穷尽。

## PendingWriteAuthority 生命周期审查

当前 `PendingWriteAuthority` 持有 `pending → consumed|revoked` 单次写入授权，消费操作在原始 Turn 身份、工具类型、用户源消息 SHA-256、同会话及 M4 WriteFence 检查后 CAS，Task + Attempt + consumed 一事务提交。显式撤销此前在 Turn finalization 同一事务内将 pending 改成 revoked。

此次新增覆盖：当已绑定 Pending Task 授权的后续请求选择 **非 `clarify_response` Primary 路由**，即使用户没有说“取消”，也在当前 Turn 终结事务内 revoke。仅缩小旧权限，不追加新工具准入，不修改模型路由。已新增 respond、reject、plan_missing 三种改话题的 HTTP + SQLite 断言，以及持旧 permit ID 直连消费失败、零 Task、零 Attempt 的断言。

特别说明：此规则不覆盖模型错误地将语义上“改话题”归类为 `clarify_response` 且文本未命中显式撤销 veto 的情况。这是语义提案/工具标题边界仍有的 residual risk；不可声称完全不依赖关键词处理任意模糊取消表达。对此可新增对抗场景测试，合理下一阶段采用更明确的 Pending 续答确认状态或来源边界；任何改动须保留正常续答可写入的业务成功路径。

## 需验证的最新整改 HEAD

新提交后必须重新跑下列 pytest，GitHub Actions 精确 HEAD 成功，才允许更新阶段裁决：

```powershell
Set-Location D:\HealthAgent
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-c | Out-Null
python -m pytest -v -rs `
  tests/test_primary_ac2_all_reviewers_http.py `
  tests/test_primary_wp_b_static_callgraph.py `
  tests/test_primary_wp_b_ac2_ac4_matrix.py `
  tests/test_primary_unified_dual_profile_lifecycle.py `
  tests/test_primary_durable_admission_http.py `
  tests/test_primary_pending_cancel_http.py `
  tests/test_primary_pending_withdrawal_guard.py `
  tests/test_primary_pending_write_postgres.py `
  2>&1 | Tee-Object artifacts/wp-c/phase4-e-ac2-pending-closure-v1.log
```

PostgreSQL 文件要在原先隔离 Docker PostgreSQL 容器及环境变量可用时运行；若无数据库则拆开跑 SQLite-only，并明示 PostgreSQL 本次未跑。当前结论：**HOLD，直到新 HEAD CI 与针对性回归、覆盖审查全部完成**。未创建 PR、未合并 main。
