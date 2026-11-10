# S25-03 Step 6-A — 历史回执与证据查询集中整改（2026-11-10）

分支：`fix/s25-03-step6-a-evidence-20261110`；PR #34（Draft）；基线来自 `audit/s25-03-step6-main-20261109` 和已合并的 `main`。

## 合同与实现

1. **G03-01 P1：M4 回执来源字段丢失。** `services/plan_dispatch.py` 在仍持有可信工具 `observation` 时调用 `bounded_projection(entry.tool_name, observation)`。持久结果仅使用 `services/evidence_store.py` 既有白名单与单字段大小上限，不保存未列入字段；`recall_memory` 只保留 `found`，不得写入实际 `value`。已有 `ok`、`error` 以及 `value` 的字符串旧格式维持兼容。历史回执不能倒推缺失字段；其历史结果仍可能 unavailable。
2. **G03-03 P2：工具版本来源。** S25-03 `project_finalized_turn` 生成 `AgentAttempt` 时复制可信 `dispatch.tool_version`，不推断当前 Registry 版本，旧 NULL 保留 NULL。只读 Evidence API 增加 `tool_version`。
3. **G03-02 P2：任务历史响应。** 新增 `history_page()` 提供最新优先、最多 100 条、`has_more`。HTTP `task-history` 使用新接口；既有内部 `history()` 保持过去最早优先的正序语义（初次 CI 揭示此兼容条件，已修复）。尚未提供 cursor 访问完整旧历史，属于明确的后续能力边界。
4. **Host/权限与恢复**：不修改 Primary 或 Host 准入、M4 claim、Dispatch state machine、Step Checkpoint CAS，也不将 Trace 或模型输出当成工具回执；历史结果来自实际受信工具观察且在数据库持久化时被投影。

## 新增验证

- `tests/test_s25_03_step6_a_evidence.py`：SQLite M4 Journal→Checkpoint→S25-03 Attempt，五类工具的允许字段/非白名单过滤/记忆脱敏；历史 NULL 兼容；真实 FastAPI HTTP 的最近 100 条历史、has_more、Attempt version 与跨用户 404。
- `tests/test_s25_03_step5_postgres_physical.py`：新增 PostgreSQL 下 M4 `utc_time` 回执→Step/Dispatch→S25-03 Attempt 的物理投影/版本/非白名单过滤测试。
- **第一次 CI 发现旧内部 `history()` 正序合同回归**：1 failed、1281 passed、5 skipped；已在 `f7463c0` 恢复内部正序，HTTP 使用独立的 `history_page`，不隐瞒这一轮失败。
- 最终精确 HEAD 与 CI 结果以 PR #34 的最新 workflow 为准。在其成功前不签收 Step 6-A，不合并 PR。

## 限制与后续验收

- 非白名单字段不落库，现有 `list_tasks` 仍受原 Evidence `tasks` 白名单和大小上限约束；若涉及高敏真实待办，生产前还应定义保留期和更严格的脱敏策略，不能因“限制大小”宣称完整隐私合规。
- 历史已经丢失的 `utc/tasks/source/definition` 不做任何伪造回填。
- 此次新增自动化 HTTP 测试使用 fake Primary 作为可控路由；不等同于真实 Qwen 的跨轮执行证据验收。
- 即使最新 CI 通过，仍建议在部署前验证真实模型的 M4 非计算工具历史回放，并审查 API 的 owner 权限、分页接口需求。
- 未获用户合并授权，PR #34 保持 Draft。
