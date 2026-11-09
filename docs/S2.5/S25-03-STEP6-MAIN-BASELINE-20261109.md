# S25-03 Step 6 — 最新 main 基线及收口审查计划（2026-11-09）

## 范围与冻结规则

此文在 `audit/s25-03-step6-main-20261109` 基于 PR #33 已合并的 **最新 main** 创建。**本分支用于 Step 6 核查，不复制/合并 S25-03 历史设计或 integration 分支，不改造 Primary/Host/M4，不直接合并 main。**

### 一、Git 分支调查

- `design/s25-03-task-state-evidence-20261022`：相对 main **ahead 70 / behind 1561**，存在大量陈旧实现和重复修改；不得直接 merge/rebase 成“最终版本”，应以主链当前行为为准。
- `integration/s25-03-mainline-convergence`：相对 main **ahead 0 / behind 660**，没有独有新工作；适合归档，不作为继续开发的基线。
- PR #28 “S25-03: Rebase Step 4 Task State & Evidence on M5/M4 Mainline” 已 merged。后续 PR #33 也已 merged，当前 main 不需要倒退到旧验收点。

### 二、在 main 已核实存在的实现

| 合同 | 位置 | 目前可核对的实现 |
| --- | --- | --- |
| 可信 Attempt 与受控投影 | `services/evidence_store.py` | prepared/succeeded/failed 状态、版本关联、参数摘要和有界结果投影；未知执行结果不可当作成功 |
| 任务状态和 Step 投影 | `services/task_state.py` | AgentTaskStateStore、revision CAS、变迁历史与成功 Attempt 绑定校验；模型 ID 自述不能直接算证据 |
| M4 持久派发转 S25-03 证据 | `services/s25_03_projection.py` | 仅投影 checkpointed、参数与 checkpoint 绑定且校验成功的 Dispatch；与任务状态在最终事务写入 |
| API 接线 | `apps/api/main.py` | `project_finalized_turn`；owner scoped `/conversations/{cid}/agent/task-state`、`task-history`、`/turns/{turn_id}/evidence` |
| SQLite 合同测试 | `tests/test_s25_03_task_state.py`、`test_s25_03_transition_evidence_boundary.py` | task revision CAS、正确与失效证据绑定、失败状态、跨会话归属证据 |
| PostgreSQL 物理测试 | `tests/test_s25_03_step5_postgres_physical.py` | M4 checkpointed Dispatch → Attempt/状态变迁；独立子进程在 prepared Attempt 后崩溃的 unknown-outcome |
| GitHub Actions | `.github/workflows/ci.yml` | 包含 S25-03 PostgreSQL 物理验收命令，PR #33 最新合并前已通过 |

**不要误把 2026-10-22 的前三步设计文档里“尚未实现”的陈述当成当前缺口**。代码与数据库经过多个 PR 演化；缺口必须重新在当前 main 审查并复现。

### 三、Step 6 应验证而非假定已完成的合同

1. **API 真实读取链**：创建计划 → FIRST / RESUME → 读 task-state、task-history、turn evidence；确保真实快照、Attempt、M4 Dispatch、transition 的关联一致，且读出的历史结果有来源，不由模型凭空重述。
2. **权限隔离**：Owner A 的 task-state/history/evidence 不得被 Owner B 通过会话或 Turn ID 访问；错误不能通过不同 API 泄漏结果。
3. **版本与修订**：旧 NULL 记录、`legacy-plan-v1`、`1.0.0` registry Attempt 不互相冒充；计划修订使旧证据失效，但不得静默改写原始证据。
4. **失败、崩溃、恢复**：未知执行结果禁止自动重派发；失败或中断不得把 Task/Step 的完成状态升级。
5. **严格证据投影**：引用、摘要、过大/敏感工具输出都应有界；记录来源、状态及失效含义。检查是否真的能安全交付历史具体结果。
6. **实证层次**：分开记录 SQLite 合同测试、PostgreSQL 并发/崩溃、API HTTP、真实模型，不能用一个替代另一个。

### 四、下一份产物的最小要求

在此基线上对 **main 当前实际代码** 做一次具体的 S25-03 Step 6 Gap 审计：引用路径/行号和可复现步骤，将问题分为 P0/P1/P2/限制；优先写针对性的 HTTP 多轮历史证据检索与跨 Owner 负例。不添加第二个状态权威，不新建 Runtime，不把全部 S2.5 重新拆分成微切片。**未证明缺口之前不修改生产逻辑。**

**当前裁决：新 main 基线建立；S25-03 已经有 Step 4/5 实现及物理测试，Step 6 仍需独立的当前主链证据审查，不能宣布整体 S25-03 完成。**
