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


## 2026-10-29 PostgreSQL isolated subprocess crash acceptance

Implemented tests/test_s25_02_m4_postgres_subprocess_crash.py, explicitly run in GitHub Actions CI as "M4 isolated PostgreSQL subprocess crash and recovery" using TEST_POSTGRES_URL against the dedicated PostgreSQL 16 CI service.

- Case A: child process persists Claim and pre-dispatch journal row (dispatch_unknown), then exits abruptly with os._exit(73) before receipt. Parent process confirms running Turn, Claim, pending plan and unknown dispatch survive. Operator-attested quarantine cancels the old plan without any tool replay, releases Claim and persists recovery audit.
- Case B: child persists Claim, calls actual local calculate(4*7), persists observed successful receipt, then exits abruptly before checkpoint. Parent sees unchanged pending plan and observed dispatch. Operator-attested receipt reconciliation commits completed plan step and checkpointed receipt, releases Claim and records audit with **no additional tool call**.
- Test cleanup is limited to dedicated test Conversation records in the isolated CI database. No actual API service or external worker is killed.
- CI run 40803054338 at HEAD 68876d85adfbbe8d19f64072dc482f3f72f66e29: SUCCESS; 686 passed / 3 skipped in aggregate; separate PostgreSQL claim-row-lock, crash/recovery, and migration steps each SUCCESS.

Coverage restriction: process termination is a spawned test helper, **not** two independently running FastAPI workers or a crash with a non-idempotent external write tool. This establishes crash durability + controlled recovery for current calculate path; it does not establish general external exactly-once or production multi-worker behavior. Next independent work: full API multi-worker kill/restart E2E with physical traces, guarded/legacy model invocation and latency comparison, then final M4 closure review.


## 2026-10-29 M4 多 API 进程与对照验收增量

新增 `tests/test_s25_02_m4_postgres_api_workers.py`，在隔离的 PostgreSQL CI service 上启动两个不同端口的 FastAPI 进程，制造数据库遗留 running Turn，终止一个测试进程，再在另一个进程和重启实例发起 HTTP 请求。目标是验证 PostgreSQL 部分唯一索引对同会话活跃 Turn 的约束、以及恢复状态接口不自动解除占用。它**尚未**覆盖真实模型执行中精确杀 worker 或外部非幂等工具行为。若 409 检查失败，不允许将 provider_error 503 视作通过。

验收基础设施要求：在 PostgreSQL CI 服务全新启动后，**先执行 Alembic `python -m services.migrate`** 再执行任意 `Base.metadata.create_all` 的测试。因为 SQLAlchemy Base 定义并不包含 0005 迁移建立的 `uq_running_turn_per_conversation` PostgreSQL 部分唯一索引；若测试先建表后直接运行 API，将无法得到生产 schema 的跨 worker 约束。已将迁移前移至 pytest 步骤之前。

新增 `evals/s25_02_m4_paired_performance.py`：仅在显式 `RUN_S25_02_M4_PAIRED_LIVE=1` 时对本机两个不同端口的真实 legacy 和 guarded 服务发等价三阶段计划，记录各请求 HTTP 总时延、P50/P95、方案功能 PASS/FAIL 和可观测的 Shadow 额外诊断调用。**此脚本不能从 HTTP 回包得知主链模型准确调用次数，标记为 NOT_INSTRUMENTED；未实际提供双服务运行证据前，不得声称性能对照完成。** 两端需相同实际模型及超时、独立 DB，建议至少 10 次循环且注意 Qwen 本机并发资源竞争；统计数字仅代表运行环境和请求集。

仅当最新精确 HEAD CI、PostgreSQL 专项均 PASS 时，将数据库级多进程状态验证标为 PASS。M4 继续 VERIFYING/HOLD，默认 legacy，不合并 main。


## 2026-10-29 PostgreSQL two-API-worker CI acceptance

Exact code HEAD `6ee71323c8444dfa2c71d0e007968779e446109d`, run `40803797015`: SUCCESS. 687 passed, 3 skipped. PostgreSQL Alembic migration is now executed **before** integration tests, ensuring the `uq_running_turn_per_conversation` partial unique index exists. Earlier CI 503 was an invalid schema setup (direct `create_all` without migration), not a demonstrated safe concurrent rejection. The previously present M3 Claim contention test is now consistent with the unique-index schema: its second test Turn is terminal, while Claim/CAS competition remains covered.

New `tests/test_s25_02_m4_postgres_api_workers.py`: starts two independent FastAPI processes against same isolated PostgreSQL CI DB; injects an orphan running Turn into a dedicated test Conversation; terminates one spawned worker; verifies HTTP 409 on the second, read-only recovery status and no replay permission; restarts the first worker and verifies HTTP 409 persists. The CI step `M4 PostgreSQL two API worker restart isolation` succeeded.

**Limit**: a running Turn is synthetically persisted by test setup, rather than deliberately killing a real in-flight model/tool invocation. Tests prove database-scoped API worker admission under a lost-owner state, not a full actual Qwen tool call interrupted during network I/O. Existing isolated subprocess crash tests independently cover durable Dispatch/Receipt physical persistence and offline recovery. Do not combine them into an unsupported claim of end-to-end killed-Qwen exactly-once.

New paired benchmark script `evals/s25_02_m4_paired_performance.py` remains **NOT RUN** on real Qwen. It requires two local services with matching model/provider/strategy and **separate databases**. Invoke using explicit `RUN_S25_02_M4_PAIRED_LIVE=1`, `--legacy-endpoint`, `--guarded-endpoint`, `--cycles`, `--output`. Main model-call totals are not instrumented in the current HTTP response, so it reports `core_model_calls=NOT_INSTRUMENTED` and separately reports observable Shadow diagnostic calls. Avoid treating a few iterations as statistically stable P95 or as proof of equal model cost.

**Gate**: PostgreSQL 2-process HTTP blocking + restart PASS; performance comparison still awaiting a real two-endpoint run; M4 VERIFYING/HOLD, no merge and no legacy-default change.


## 2026-10-29 Real Qwen legacy-vs-guarded comparison (five cycles each)

Evidence: user-supplied `m4-paired-comparison.json` (schema `s25-m4-paired-performance-v1`), provider `ollama_proxy`, model `qwen3:14b`, distinct endpoints/modes, five three-request plan lifecycles per mode.

- Legacy: 4/5 complete lifecycle functional passes, 15/15 HTTP 200; cycle 0 FIRST returned `goal_clarification` (zero tool calls) instead of executing only the first step. Its REMAINING then ran two calculator steps in one turn, so completion did **not** satisfy requested staged execution sequence.
- Guarded: 5/5 functional passes, 15/15 HTTP 200, CREATE saved two pending steps, FIRST one calculator call, REMAINING one calculator call, no repeated first step.
- Overall request latency: legacy mean 6789.5 ms, P50 7839.0 ms, P95 10276.6 ms; guarded mean 6361.6 ms, P50 7842.0 ms, P95 8431.1 ms. Guarded observed mean is 6.3% lower, but cannot be interpreted as statistically proven speed gain.
- Both Shadow diagnostic observations are empty (Shadow off); `core_model_calls=NOT_INSTRUMENTED` for both modes. Request latency includes model response and other runtime time. The current runner executes all legacy requests before all guarded requests rather than alternating by cycle despite its comment, introducing warmup/order bias. Five cycles and one legacy first-cycle cold-start/outlier are insufficient for reliable P95 or cost conclusions.
- **Decision**: M4 guarded plan semantics PASS in this small local Qwen test; no clear HTTP median regression observed. Performance / model-call overhead remains VERIFYING, not quantified enough for permanent route switch. Keep M4 HOLD, default legacy unchanged, no main merge.


## 2026-10-29 Paired benchmark corrections (M4 final acceptance preparation)

The first five-cycle user run was NOT a clean randomized speed comparison: legacy had 4/5 staged-function passes while guarded had 5/5, and legacy ran first for all cycles. The benchmark was corrected to run alternating cycle order, AB then BA, retaining separate conversations/databases and the same Qwen model. The new JSON contains `cycle_orders` showing actual order.

A request-local `ContextVar` counter records attempted provider HTTP sends in the three known adapter paths: `HTTPUnderstandingModel.complete`, `NativeToolDecision.decide` and streaming providers. HTTP response headers `X-HealthAgent-Model-HTTP-Attempts` and `X-HealthAgent-Shadow-HTTP-Attempts` hold aggregate request counts without including prompts, arguments, keys or model outputs. The benchmark now reports `provider_http_attempts` plus `core_provider_http_attempts` (total minus observable Shadow attempts) and `shadow_http_attempts`. Missing headers are preserved as missing (not zero). Provider retries count as additional attempts; logical model invocations, token usage and cost remain NOT INSTRUMENTED. Endpoint settings, shadow enabled/disabled, and machine resource sharing must be kept comparable.

No new Qwen benchmark evidence exists yet for the corrected script; the earlier JSON cannot be reinterpreted as if it had measured these counters or alternated order. CI tests validate software behavior only. M4 remains VERIFYING/HOLD; no merge to main and no switch from legacy default.


## 2026-10-29 M4 paired Qwen benchmark v2 (10 AB/BA cycles)

Evidence: user-supplied m4-paired-comparison-v2.json, with 10 alternate cycle orders AB/BA, each mode 10 separate three-request plan lifecycles, both running ollama_proxy qwen3:14b, Shadow HTTP attempts zero. Per-mode provider HTTP counts recorded for all 30 requests.

- Guarded: 10/10 functional passes; 30 HTTP requests, 240 provider HTTP attempts, mean 6364.4 ms, P50 7842.5 ms, P95 8402.8 ms.
- Legacy: 9/10 functional passes; 30 HTTP requests, 235 provider HTTP attempts, mean 6565.2 ms, P50 7834.5 ms, P95 8389.8 ms.
- Legacy cycle 0: CREATE 14235 ms / 5 provider attempts; FIRST returned goal_clarification and performed no tool call (7221 ms / 7 attempts); REMAINING executed both tools together (3660 ms / 7 attempts). This violates requested first-step-only execution even though both results eventually complete.
- Normal cycles in both modes use 7/10/7 = 24 provider HTTP attempts per lifecycle. Hence guarded's five additional attempts in aggregate arise entirely from the failed legacy cycle; no extra provider HTTP attempt cost was observed for correct staged lifecycles.
- Response median and P95 are essentially equal. Guarded average is 3.1% lower, but the legacy first-cycle 14.2s outlier and sample size preclude claiming statistical performance improvement. Counts are HTTP attempts, NOT tokens, full model cost, or logical model-call IDs.
- Scope: results demonstrate improved staged plan semantics and no observed latency/call regression on this single calculation scenario. They do NOT establish generalized multi-tool correctness, external side-effect exactly-once, or a production-default switch. Prior crash and PostgreSQL tests remain separate evidence. **M4 VERIFYING / HOLD pending final independent code/permission/merge-readiness review**. Default legacy and main unchanged.
