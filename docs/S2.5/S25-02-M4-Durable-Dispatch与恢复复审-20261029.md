# S25-02 M4 Durable Dispatch / Receipt 与离线恢复（2026-10-29）

**开发分支**：feat/s25-02-m4-guarded-execution-20261028。
**安全门禁**：默认 legacy 保持不变；此功能仅在 guarded 已保存计划执行路径使用。不得自动 merge / 切换默认。

## 故障边界与实现

- Alembic 0009 创建 `plan_tool_dispatches`：在工具执行前，以数据库事务写入 `dispatch_unknown`，保存 Turn、Step、工具和精确参数；工具返回后单独写入 `observed`（成功）或 `observed_failure`（不可靠失败回执），并记录有限字段回执。
- Checkpoint 与成功 Receipt 转 `checkpointed` 在相同数据库事务内完成。如 Checkpoint CAS 冲突或工具调用后进程崩溃，Dispatch 留在未完成状态；后续 guarded 执行请求不得再次调用该步骤。
- 当前工具 API 不支持外部幂等键/结果查询。这实现了可审查的**发起前意图与观察回执**及保守 **no automatic replay**，不是外部副作用 exactly-once 保证。持久意图存在但回执缺失时，不应推断工具已执行或未执行。
- Alembic 0010 创建 `plan_recovery_audits`，记录离线恢复的操作者、Turn、快照哈希、证据文件哈希与动作；不储存凭证。
- 崩溃导致的 running Turn/Claim：正常 API 继续 fail-closed，`GET /conversations/{cid}/plan/recovery-status` 可只读查看；不自动释放运行锁。
- 新增 `services/m4_offline_recovery.py` 的两种**人工离线**处理，仅可在所有 API/worker 进程实际停止、独立核对后使用：
  - `--resolution reconcile_receipts`：仅适用 **已有可信成功回执、同一 Turn/Step/参数精确绑定的 calculate**。按当前快照做 CAS 核对，在同一事务内标记已完成步骤、Receipt checkpointed、结束遗留 Turn 和 Claim，并追加审计。**不再执行计算工具**。其他工具和缺失回执一律拒绝。
  - `--resolution quarantine`：没有可信结果时，人工隔离并取消当前不确定计划，释放遗留 Claim，记录 `quarantined_no_replay` 与审计；**不标记任何原步骤成功，也不自动重试**。隔离后，是否重新做业务操作必须人工判断外部效果。
- CLI 使用 `--conversation-id`、`--turn-id`、`--snapshot-sha256`、`--operator-id`、`--evidence-file` 及 `--confirm-all-workers-stopped`。运行前须由维护人员确保服务真的停止，工具无法仅凭 CLI 参数证明该事实。过期计划 hash、多个不确定 Turn、Claim 归属不符、回执不匹配均拒绝。

## 人工核对文件示例

离线维护人员填写 JSON 文件 `operator-evidence.json`（示例中的 ID 必须替换成数据库真实值）：

```json
{
  "decision": "reconcile_verified_receipts",
  "conversation_id": "真实会话UUID",
  "turn_id": "真实Turn UUID",
  "operator_id": "明确可审计的维护人员标识",
  "external_effect_investigated": true,
  "all_workers_stopped": true,
  "investigation_note": "已查看数据库日志和工具调用记录，确认该 Turn 的计算成功回执可信。"
}
```

如要放弃旧计划，改为 `"decision": "quarantine_without_replay"`。**该声明必须属实，不可为了通过脚本而填写 true。**

离线恢复示例（注意：先关停并确认所有服务与 worker；非沙箱环境先备份数据库）：

```powershell
python -m services.m4_offline_recovery --database-url "sqlite:///./healthagent.db" `
  --conversation-id "真实会话UUID" --turn-id "真实Turn UUID" `
  --snapshot-sha256 "准确的原始计划snapshot SHA256" `
  --evidence-file operator-evidence.json --operator-id "维护人员标识" `
  --resolution reconcile_receipts --confirm-all-workers-stopped
```

上述命令**只用于真实故障后的授权维护，不是正常验收步骤**；不要在真实 Qwen 普通测试后随意运行它。

## 新增物理验收（正常路径）

完成当前代码与数据库迁移后，重新启动 guarded Qwen 服务，并**重新执行**：

```powershell
$env:ROUTE_ADMISSION_MODE = "guarded"
$env:RUN_S25_02_M4_GUARDED_LIVE = "1"
python -m evals.s25_02_m4_guarded_live --output m4-durable-live.json
python -m evals.s25_02_m4_durable_physical --database healthagent.db `
  --live-report m4-durable-live.json --output m4-durable-physical.json
```

物理读回只使用 SQLite；如果 FastAPI 使用 PostgreSQL，不能用此脚本作数据库核验，需对应 PostgreSQL 读回或额外集成测试。旧版 `m4-guarded-live-v3.json` 没有新日志行，不可拿来代替新测。

## 独立审查结果与生产限制

1. 支持的读工具路径已具备持久意图、回执和检查点原子终态。受控异常、快照失配、崩溃后遗留状态及离线恢复有确定性测试，最新 CI 结果以精确 HEAD 为准。
2. 不支持在工具回执缺失时自动推进 Checkpoint，也不支持外部写入工具的真正 exactly-once（需要外部工具接收并持久化幂等键，且可按键查询效果；或可验证的事务性协议）。
3. 离线恢复需要维护人员实际停止全部 worker、证明权限和执行效果；当前 CLI 的 `operator_id` 属于审计标识，不是独立管理员认证。**不得在面向不可信终端的共享主机上开放该 CLI**。
4. 目前没有完成 PostgreSQL 多 worker kill/restart 物理验收、Qwen 接入新 Dispatch/Receipt 的本机物理证据或 legacy/guarded 完整延迟对照。M4 仍 HOLD，不应宣告生产级完整关闭。



## 2026-10-29 真实模型与 SQLite 物理验收（用户本机）

用户上传 `m4-durable-live.json` 与 `m4-durable-physical.json`；审查输入是两个文件中的逐项原始报告，不是 CI 的模拟测试。服务自报 `ollama_proxy / qwen3:14b / guarded / guarded-plan-route-priority-v2`。

- **真实 API：5 PASS / 0 FAIL / 0 BLOCKED**。CREATE 保存 `3*4` 和 `5*6` 为 pending，不提前调用工具；FIRST 一次 `calculate(3*4)=12`，第一步 completed、第二步 pending；REMAINING 一次 `calculate(5*6)=30`，两步 completed，第一步无重复派发；CANCEL_PREPARE 独立建立两步待取消计划、CANCEL 成功取消，两者均无工具调用。
- **物理 SQLite：9 PASS / 0 FAIL**。`dispatch_migration` PASS，证明读回库处于 Alembic `0010_plan_recovery_audit`；FIRST、REMAINING 各一条同 Turn、同 request_id 的 Dispatch，工具名称、表达式、回执值与 `checkpointed` 精确匹配；CREATE、CANCEL_PREPARE、CANCEL 均无 Dispatch；`PRAGMA quick_check` 为 ok。
- Shadow：所有五个场景 `state=valid`；创建、取消为 `comparison=match`；FIRST 与 REMAINING 为 `compatible_plan_execution` 而非严格 `match`。Shadow-only `m3c_admission` deny 及 `execution_authorized=false` 是诊断契约，不代表正式 guarded 路径未执行。
- **结论：M4 guarded 已保存计划正常路径的真实 Qwen3 + SQLite Durable Dispatch/Receipt 验收 PASS**，此前缺失的新版本实测证据已补齐。
- **不得外推**：报告自身仍要求确认实际 API `DATABASE_URL` 与物理读回的 `healthagent.db` 是同一库；本轮未执行真实 OS kill/重启故障、多 worker PostgreSQL 端到端读回、外部具写入副作用工具的 exactly-once，且未做 legacy/guarded 同题时延与模型调用成本对照。离线人工恢复仅有确定性测试，尚未进行独立真实故障演练。
- **门禁：M4 仍 VERIFYING/HOLD**。继续默认 legacy；只有完成剩余的多进程崩溃恢复物理证据和独立性能/权限审查后才讨论 M4 关闭，不自动合并 main。
