# Primary Routing Convergence — AC1–AC5 独立集中验收报告（2026-11-03）

> 裁决：**HOLD / 不准合并**。本报告为只读代码与已有 CI 证据的集中复审，不是 Qwen3:14b 真实物理验收，也不修改冻结合同。

## 1. 精确基线与证据边界

- 仓库：`cxjchelsea/HealthAgent`
- 分支：`refactor/primary-routing-convergence`
- 审计代码 HEAD：`1a6949c29a96ea592ecefc54d10e45cf1201c2f0`
- 基线：`main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`
- 对比：审计时开发分支 ahead 117 commits、behind 0、50 个变化文件。
- GitHub Actions：`41583562882`，`1036 passed, 3 skipped`（主 pytest）；后续 PostgreSQL 专项、基准测试、compileall、Ruff、mypy 与 Alembic CI job 全部 success。
- 自动化环境：Ubuntu GitHub Actions、Python 3.12、PostgreSQL 16。真实模型路由的 HTTP 测试普遍使用确定性 Primary 提案桩，不能据此宣称真实 Qwen 语义通过。
- 合同：`docs/primary-routing-convergence/ACCEPTANCE_CONTRACT.md` v1.2。

## 2. 冻结合同逐项裁决

| 合同 | 裁决 | 已验证证据 | 阻断事项 |
| --- | --- | --- | --- |
| AC1 单一语义所有者 | **HOLD** | `apps/api/main.py` 在真实模型 auto/explicit Unified 调用 `propose_primary_route`，失败后不回退 Legacy；路由 HTTP 回归覆盖正常和拒绝 | 缺少每个 Turn 的**durable proposal_id/schema_version/route_kind/semantic_attempts 与绑定的 preturn snapshot ID**。仅在 response `trace` 回显，不满足冻结合同的持久化要求；无权威可查询记录证明唯一 Primary 的全生命周期 |
| AC2 13 个 Legacy Reviewer 零调用 | **PARTIAL PASS / 整体 HOLD** | `tests/test_primary_ac2_all_reviewers_http.py` 对 13 名单、UnderstandingService、旧 M5 和执行策略建立 fail-on-call 探针；auto/explicit Unified 的 CREATE、CANCEL、write、respond、reject、pending MCP 均通过，另有 FIRST/RESUME/REVISE 等专项测试 | 尚未形成覆盖**全部路由状态、异常/澄清/权限拒绝、并发重放**的函数级计数记录和 AST/调用图证据；`model_call_metrics` 是请求级分类而非不可变的函数级 durable 证据 |
| AC3 Host 一次逻辑准入、只能收窄 | **PARTIAL PASS / 整体 HOLD** | `agent/primary_host_admission_v1.py` 一次确定性决策；`apps/api/main.py` 在 `deny/restrict` 下终止，显式 ordinary 只可收窄；覆盖权限拒绝、快照校验、冲突及写入无副作用测试 | 缺少 **proposal→admission 不可变的持久化关联**，仍无法完整重建跨崩溃的准入决策；对极端交错/状态穷举不能以现有案例替代 |
| AC4 统一执行与副作用证据治理 | **PARTIAL PASS / 整体 HOLD** | `agent/primary_ordinary_write_v1.py`、`services/primary_pending_write.py` 共享写入栅栏；`agent/primary_pending_readonly_v1.py` pre-dispatch journal；M4 saved-plan claim/journal/CAS；PostgreSQL 双会话、独立进程崩溃、重放专项 CI 均成功 | 跨**所有工具类型/AgentLoop 多步骤/全部异常路径**的一致性矩阵未穷尽；外部工具 exactly-once 未证明；`prepared/unknown` 只可核对，不能假设已提供自动安全恢复 |
| AC5 兼容/真实模型验收 | **HOLD** | 1036 pytest passed / 3 skipped，覆盖计划生命周期、Pending、普通任务和记忆写入、显式计算、回归安全围栏 | **没有对应本 HEAD 的实际 Qwen3:14b + Ollama Proxy 物理请求/输出记录、未知挑战集、成功率及差异分析**；不能宣称已具备真实模型能力完整性 |

## 3. 具体缺口（按优先级）

### P0-A：Primary durable 语义与准入证据链缺失（AC1/AC3）

- `services/db.py` 的 `Turn` 仅保存状态、request_id、failure_code、decision_steps、计时等，没有 proposal/admission 关联字段或等效的不可变记录表。
- `apps/api/main.py` 约 361–401 行产生 Primary Proposal 和 Host Admission；约 585–601 行拼装 trace；约 1430–1540 行原子终结 Turn；当前持久化提交没有写入合同要求的规范语义证据。
- `/conversations/{cid}/turns/{turn_id}/diagnostics` 仅返回 Turn 状态、failure_code、decision_steps、duration_ms；`/evidence` 主要返回工具 Attempt，均不能替代 `proposal_id/schema_version/route_kind/semantic_attempts/snapshot_id/admission` 的 durable 绑定。
- 修复建议：新增**受 Turn 唯一约束**的不可变 Primary Route Evidence 记录（避免变更已有 Turn 对外契约）；记录 canonical route、规范版本、模型调用计数/原因码、preturn snapshot digest、Host disposition/reason、关联 Turn。保存发生在任何执行副作用之前；状态必须能表达拒绝/未能生成/已提交与未知结果。不要存敏感原文或 token。测试 crash-after-proposal、crash-after-admission、retry、cross-turn CAS、owner-only 读取。

### P1-B：13 项零调用覆盖与全状态调用图（AC2）

- 目前已具备两种真实模型配置下的 fail-on-call HTTP 回归，证明其**覆盖样本**不进入旧审查器，不等于覆盖全部路径。
- 修复建议：将 AC2 全名单函数级调用清单和 route_kind × pending/plan × disposition × exception 矩阵落为自动验收；正常、缺失计划、撤销、修改、澄清、pending 写入、拒绝、重放和失败场景都要在证明没有二次语义模型的同时维持业务成功率。
- 显式 `routing_profile=legacy` 保留旧分类器属于合同允许范围。Fake-provider `routing_profile=unified` 暂仍作为旧兼容测试夹具，需注明退出或隔离条件；不能把它冒称完整 Unified 合同通过。

### P1-C：真实 Qwen3:14b 物理验收（AC5）

- 需要独立的、此前未纳入 prompt 调参的挑战请求集；保存请求标识、模型类别计量、规范 Primary route、Host disposition、Turn/Attempt/Plan CAS 证据、实际结果和失败原因。
- 至少覆盖 CREATE/FIRST/RESUME/REVISE/CANCEL、ordinary calculate/MCP、create_task/save_memory (含 consent deny)、Pending MCP/create_task、unknown plan、无效 schema、并发及重放。
- 所有测试须绑定测试时的**精确 HEAD**，列出基线差异；一次性模型成功不能替代可重复的成功率与拒绝质量评估。没有模型服务时标为 NOT RUN，不以 mock 代替。

### P1-D：执行合同全入口完备性（AC4）

- 现有专项证明跨普通/Pending 写入锁与 PostgreSQL 崩溃边界在所测范围有效；`AgentLoop` 与外部 MCP 的所有异常路径及未知结果处置仍应走集中矩阵。
- 特别核对 `read_only`、explicit selection、response_only、pending resume、save_memory、plan resume 对权限放大、prepared 未完成、跨 Turn 重放与 operator reconciliation 的行为；禁止未核实的未知结果自动重派发。

## 4. 本轮独立审查事实与限制

- 当前真实模型 auto/explicit Unified 在 `Settings.primary_route_v1_active` 下进入 Primary；fake + explicit unified 仍保留兼容运行方式。
- Unified Primary 正常执行路线先经过 deterministic Host（`primary_host_admission_v1`），写入和 Pending 走已有封闭执行器。**没有在审查的代码中发现 AC4 既有两项 P0 写入竞态修复再次被撤销的证据**。这不是未测并发下不存在缺陷的证明。
- `primary_route_v1.py` 对 unknown enum、schema 与来源 quote 有严格验证；`plan_missing` 由 Host 限制为不执行。
- `primary_route_v1_canary.py` 仍是计划 create/revise/cancel/clarify 的现有 Host 操作分派，文件名保留“canary”不代表这些操作未被正式路由调用。
- 无法从 GitHub CI 的 mock/fixture 推出本地 Windows + Ollama Proxy 的模型语义行为、时延和实际可用率。
- 本报告只记录审查，不代表通过了未实际运行的物理测试。

## 5. 下一工作包与合并门禁

按顺序：
1. **WP-A Primary durable proposal/admission evidence**：先实现唯一、不可变、预副作用写入的语义与 Host 证据；迁移数据库，完成 PostgreSQL/崩溃/重放测试。
2. **WP-B AC2 完整状态矩阵 + AC4 闭环覆盖**：精确函数级计数与调用图，允许 Legacy 仅显式隔离；补足异常、权限、Pending、多工具负样本。
3. **WP-C Qwen3:14b 实际物理验收**：用非训练样本完成成功率与异常分析，再决定是否调整模型提示词或执行器。
4. 只有 AC1–AC5 全部取得精确 HEAD 的可复验证据，CI/PR 保护核验通过，并取得用户**明确合并授权**，才能执行 Standard Merge Commit。

**最终裁决：HOLD；不可合并到 main。**
