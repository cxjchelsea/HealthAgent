# WP-C Phase 4-C — PostgreSQL 并发竞争独立验收归档（2026-11-04）

## 精确证据与结论

- P4-C 原始验收 HEAD: `738dc472eb5a4d666f2d21d1e1d976e7b4ef3511`。Windows Python 3.13.5、隔离 Docker PostgreSQL 16-alpine，用户本地执行 `phase4-c-postgres-v1.log` 为 **5/5 PASS**；`phase4-c-process-v1.log` 为 **7/7 PASS**；环境记录 `phase4-c-environment-v1.json` 对应同一 HEAD、端口 55432。上述原始日志来自对话上传，**不在 Git 仓库中**。
- 增补授权撤销 vs 消费竞争测试提交 `dd6e733826143244d33e0ee540944f1dcc334587`，本地 `phase4-c-revoke-consume-v2.log` **3/3 PASS**，包括已有双消费者与新加的 `revoke` / `consume` 两种确定性交错。日志原件仍由用户保管，非仓库内原始附件。
- 精确该 HEAD GitHub Actions: [41762843589](https://github.com/cxjchelsea/HealthAgent/actions/runs/41762843589) **completed/success**，与本地通过证据相互独立。
- **裁决：Phase 4-C 已定义的 PostgreSQL 专项测试范围 PASS**。不能把 5+7+3 计为 15 个独立测试用例（原双消费者重复执行）；也不将独立 Sessions 的线程测试虚称为跨进程 HTTP revoke/consume 竞争。

## 验证的不变量

- 相同 Pending 授权双消费者竞争只允许一次 create_task 成功。
- 普通写与 Pending 在相同会话共享写入围栏，另一侧不能产生第二条任务。
- Primary Evidence 同 Turn 竞争仅允许一条持久准入；崩溃后不可覆盖。
- Pending 消费先于撤销提交时：授权 consumed、有且仅有一条任务/成功回执；撤销冲突。
- Pending 撤销先于消费提交时：授权 revoked、零任务和成功回执；消费被拒。
- 现有独立 PostgreSQL 崩溃/重启专项均通过，但其具体 unknown outcome 的 Phase 4-D 最终裁决单列。

## Phase 4-D 需复审而非推倒重构

1. 现有 `tests/test_primary_ac4_postgres_process_crash.py` 的四种受控子进程硬退出：`ordinary_inside` / `ordinary_after` / `pending_inside` / `pending_after`。
2. `AgentAttempt.status=prepared` 必须一直诊断为 `unknown_outcome`，不自动二次派发；仅有 succeeded 本地回执且 Turn 未 completed 必须是 `local_receipt_unfinalized`。
3. 在新分支代码中，合法已撤销的 `PendingWriteAuthority.status=revoked` 要有明确只读审计终态，非法 revoked+consumed_turn_id 不能被误报为合法撤销；此诊断修复属于 Phase 4-D，不追认 P4-C 原始 HEAD。
4. 核查 M4 saved-plan dispatch_unknown、请求重放、API 重启后的权限/证据关联；对无法证明外部效果的工具保持 unknown 与人工对账，不宣称 exactly-once。
5. Phase 4-D 完成后仍需 AC1–AC5 精确最终 HEAD CI/功能回归和用户授权才能合并 `main`。

**范围限制**：本验收不证明外部非幂等系统 exactly-once、不证明所有自然语言撤销表达或生产级权限配置安全。