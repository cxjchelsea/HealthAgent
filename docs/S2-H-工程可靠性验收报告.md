# HealthAgent S2 H 系列工程可靠性验收报告

- 审计基线：`main` merge SHA `777560711c0d0fa2cf11293a673daea9b1766f56`
- 测试分支：`test/s2-h-reliability-acceptance-20261021` / PR #14
- 范围：H01–H10，自动化优先；不修改 Agent 主链，不使用真实患者数据
- 证据级别：GitHub Actions Python 3.12 + SQLite 单机 / PostgreSQL 16 迁移烟测；真实多进程崩溃、PostgreSQL 并发冲突和实机 DB 故障不作无依据的 PASS 断言

## 测试映射与准入要求

| ID | 预期结果 | 自动化证据 | 边界 |
|---|---|---|---|
| H01 | 同会话同时请求，第二个 HTTP 409 | `tests/test_s2d_concurrent_turn_api.py::test_same_conversation_conflict_and_release` | SQLite 并发请求；PostgreSQL 真并发独立证明尚不足 |
| H02 | 同一 `client_request_id` HTTP 409、不产生重复 Turn | `tests/test_s2_h_reliability_acceptance.py::test_h02_duplicate_id_does_not_create_second_turn` | SQLite API/DB 实证 |
| H03 | 跨用户访问会话 404 | `tests/test_s2d_api_multiturn.py::test_api_multiturn_pending_and_completed_correction` | 两个测试身份 |
| H04 | 认证关闭演示模式时无 token 返回 401 | `tests/test_s2_memory_management_ui.py::test_memory_management_api_exact_keys_and_isolation`、`tests/test_s2_turn_diagnostics.py` | 测试 Bearer 配置 |
| H05 | 记忆写入需授权，不得以文本同意替代工具权限 | `tests/test_s2_h05_memory_consent.py`：真实 Conversation API 分别以 consent=false/true 调用 save_memory、验证 DB 不变/更新 | SQLite API，需最新 CI 验证 |
| H06 | 计划持久化失败不可泄露成功/部分提交 | `tests/test_s2_h_reliability_acceptance.py::test_h06_checkpoint_commit_failure_never_leaks_success` | 故障注入 SQLite；失败后 Turn 仍 running，要求 H07 人工恢复，不是自动恢复 |
| H07 | Worker 确认停止后，dry run 不修改数据库，apply 将超龄 running 标 interrupted，不重放 | `tests/test_s2_h_reliability_acceptance.py::test_h07_dry_run_then_manual_recovery_is_non_replaying` | 仿真遗留 running Turn；并非物理 kill -9 |
| H08 | 拒绝无效/重复 ID、缺失成功证据的 Checkpoint | `tests/test_s2d_resume.py::test_invalid_checkpoint_fails_closed`、`tests/test_s2d_stable_step_ids.py` | PlanLedger 单元/SQLite |
| H09 | 部分废止、恢复后 Step ID 稳定不重用 | `tests/test_s2d_stable_step_ids.py` | SQLite checkpoint |
| H10 | Alembic 重复执行不丢数据 | `tests/test_migration.py`；CI PostgreSQL 16 连续运行两次 `python -m services.migrate` | CI PostgreSQL 为迁移烟测，不等同 PostgreSQL 并发 |

## 执行方式

```powershell
cd D:\HealthAgent
git fetch origin
git switch test/s2-h-reliability-acceptance-20261021
git pull --ff-only
.\.venv\Scripts\Activate.ps1
python -m pytest -q
python -m pytest -v tests/test_s2_h_reliability_acceptance.py tests/test_s2d_concurrent_turn_api.py tests/test_s2d_resume.py tests/test_s2d_stable_step_ids.py tests/test_migration.py
```

## 判定与边界

- 最新 PR #14 的精确 HEAD CI 必须最终为 **SUCCESS** 才能确认自动化回归通过；不得以在途 CI 当作通过。
- H01 PostgreSQL 真实竞争，以及 H07 真实进程崩溃/外部副作用不重放，仍为更高级物理验收范围。
- H06 注入提交失败后留有 running Turn 是符合现有原子回滚的状态，但需要显式人工恢复；不是系统自动清理。
- H05 独立用例覆盖请求级 consent 的数据库不变性；仍不能替代真实模型能否自主选择正确工具的评测。
- S2 总体完成须结合 S2-A–D 真实模型与人工验收证据，不能仅凭 H 类自动化通过宣布 S2 CLOSED。
