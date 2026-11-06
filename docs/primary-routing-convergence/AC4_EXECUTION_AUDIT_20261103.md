# AC4 执行治理独立审计 — 2026-11-03

## 复审基线

- 分支：`refactor/primary-routing-convergence`
- 经验证的代码提交：`b06957f45509af8033302f19f4b641c4d6b6fe17`
- GitHub Actions：`41571169130`，996 passed / 3 skipped，全部检查通过
- 运行方式：GitHub Actions Python 3.12 + PostgreSQL 16。此记录**不是**真实 Qwen3:14b、Windows 本地或默认 Unified 物理验收

## 审计发现及本轮修复

**F-AC4-01（已修复，回归通过）**：普通写入原先在一个事务内检查未解决副作用，再在另一个事务写入 `prepared`；Pending 写入也各自检查，两个入口之间存在 TOCTOU 并发窗口。已新增 `services/write_effect_fence.py`，通过同一会话的 PostgreSQL 行锁在**同一写入事务**中检查阻断状态，并对普通工具建立 `prepared`，对 Pending 完成授权 CAS/效果/回执提交。

**F-AC4-02（已修复，回归通过）**：`AttemptEvidenceStore.has_unresolved_writes` 曾不把 `partial` Turn 的成功写入作为未完成证据，而其他治理入口会阻断。这会给不同路径不同的重试结论。现统一为：任何 `prepared` 写入、或者 `succeeded` 且 Turn 尚未 `completed` 的写入，都要求核对。

## 执行入口治理矩阵

| 路径 | 模型权限 | Host 准入 | durable evidence | 重试/恢复边界 |
| --- | --- | --- | --- | --- |
| 计划 FIRST / RESUME | 仅提案 | M4 guarded execution | PlanExecutionClaim + PlanToolDispatch + checkpoint CAS | 未知结果阻断，仍由 M4 治理 |
| 普通 read_only | 仅提案 | read_only AgentLoop | AgentAttempt journal（按具体工具） | 只读限制，不得写入 |
| 普通 create_task / save_memory | 绑定用户原文 | v1 Host，memory 需当轮 consent | 先 `prepared`，本地效果 + 回执原子提交 | 本轮共享会话写入锁；未完成 Turn 禁止自动重发 |
| Pending MCP 查询 | 来源核验 | read_only Pending | 先记录 `prepared`；之后记录查询回执 | 回执未知不自动重发 |
| Pending create_task | 原始 Turn 和回复双重绑定 | 持久授权 + v1 Host | 单次授权 CAS + 本地任务 + 成功回执原子提交 | 同会话共享锁，重复消费/重放拒绝 |
| Pending save_memory | 不授予 | restrict | 不适用 | 未开放 |

## 自动化证据

- `tests/test_primary_ac4_cross_entry_postgres.py`：两个独立 PostgreSQL Session 争用同一会话；普通写入先进入事务时，Pending 写入不允许产生第二份效果，最终任务与成功回执各一条，并保留 Pending 授权
- `tests/test_primary_pending_write_postgres.py`：同一 Pending 授权的两个并发消费者仅一人可成功
- `tests/test_primary_ordinary_write_v1.py`：普通写入事务失败后留待核对，不自动重发
- `tests/test_primary_pending_write_v1.py`：原始授权与来源绑定、单次消费、事务回滚和重放拒绝
- `tests/test_primary_write_reconciliation.py`：只读核对区分本地回执与 Turn 最终状态

## 尚未证明（不得视为 AC4 总体验收通过）

1. 默认 Unified 主链尚未迁移到 v1；不能把 Canary 的验证等同于 AC1–AC5 收口
2. 外部工具及多步骤 AgentLoop 的权限和副作用治理尚未经过统一的精确入口计数、跨入口覆盖与真实模型验证
3. 目前有只读 `unknown_outcome` 核对，但没有受授权的操作员状态恢复；不得据此清除 `prepared` 或自动重派发
4. 跨进程硬崩溃、不同数据库隔离级别和高并发压力测试没有全面覆盖；本轮新增的是 PostgreSQL 双 Session 竞争测试
5. 不证明外部工具 exactly-once；仅证明特定本地数据库事务的一致性

**下一阶段门槛**：集中核对 v1 Canary 的双向竞争、跨进程崩溃，以及默认 Unified 入口语义分类器的调用清零证据；然后执行 AC1–AC5 的真实模型物理验收。不得为了通过验收而关闭有效的业务路径。
