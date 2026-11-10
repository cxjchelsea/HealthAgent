# S25-03 — main@9e2e79a 最终完整性审查（2026-11-10）

> **审查裁决：VERIFYING（功能实现在约定范围内基本就绪，仍有两项完整性验收证据需要补齐）。** 本文不改运行逻辑，不宣布 S25-03 CLOSED，不反向修改已合并的 PR #28/#34。
>
> **精确基线：** `main@9e2e79a473089be47d25d1c10b7a9fa82f06d89b`（PR #34 已合并，compare `main` 与该 SHA = identical）。本审查分支 `audit/s25-03-final-completeness-20261110` 仅增归档。静态审查依据：`docs/S2.5/S25-03-任务状态与执行证据管理-前三步设计.md` 的 TS01–TS12、Step 4–6 候选合同，`docs/S2.5-通用自主Agent能力补齐开发策略.md` 的分能力收口门禁，以及以下 main 文件/测试。验收记录与实现代码区分对待。

## 1. 已经落地，不能再以旧设计文档描述为缺口

| 能力 / 合同 | main 代码和回归证据 | 裁决 |
| --- | --- | --- |
| 持久 Task/Step 状态、版本 CAS | `services/task_state.py` 的 `AgentTaskStateStore`、`tests/test_s25_03_task_state.py`、`test_s25_03_state_transitions.py` | 已实现并有自动化 |
| Attempt 的可信身份、参数摘要、有界结果、prepared/unknown-outcome、终态不可重写 | `services/evidence_store.py`；`tests/test_s25_03_attempt_evidence.py`、`test_s25_03_prepared_attempt.py` | 已实现并有自动化 |
| M4 已 checkpointed Dispatch → 同事务 S25-03 Attempt/Transition | `services/s25_03_projection.py` + `apps/api/main.py` `project_finalized_turn` 接线；`tests/test_s25_03_step5_postgres_physical.py` | 已实现，PostgreSQL 实测 |
| 保存计划工具的实际历史观察字段可有界持久化 | `services/plan_dispatch.py` 调用 `bounded_projection`；`tests/test_s25_03_step6_a_evidence.py` | 已修复并有自动化 |
| 工具版本的可信传递（旧 NULL 不伪造） | `services/s25_03_projection.py` `tool_version=dispatch.tool_version`，HTTP Evidence 返回版本 | 已实现并有回归 |
| 任务历史查询、Owner 隔离 | `/conversations/{cid}/agent/task-state`、`task-history`、`/turns/{turn_id}/evidence`；`tests/test_s25_03_step6_a_evidence.py` 的 102 条历史与双 Owner 404 | 已实现并有 HTTP 测试 |
| 计划修订后移除旧 Step 的历史变迁 | `AgentTaskStateStore.save_in_session` 生成 `superseded` 事件；`tests/test_s25_03_state_transitions.py` | 已实现并有单元/存储测试 |
| 实际 Qwen 通过正式 HTTP 创建计划/执行 FIRST/读取 MCP 历史结果 | `evals/s25_03_step6_a_live.py`；本机真实 `ollama_proxy/qwen3:14b` 2/2 PASS（2026-11-10），受信本地 MCP mock catalog 与隔离 SQLite | 定向真实模型验收通过 |

PR #34 最终 HEAD `af6a716` 的 Actions run `42739035474` **1290 passed / 5 skipped**、PostgreSQL 专项 **6 passed / 2 skipped**；为合并前 CI，不能假称它覆盖当前 audit 分支或所有未执行场景。PR #28 与 PR #34 都已 merged。

## 2. 以最初 TS01–TS12 为准的覆盖矩阵

| 原场景 | 证据情况 | 完整性裁决 |
| --- | --- | --- |
| TS01 实际成功工具与可靠 Step 完成 | M4 成功回执、Attempt 绑定、Step Transition；真实 Qwen FIRST | **COVERED**（首步/MCP） |
| TS02 同一步失败重试/新尝试不覆盖旧观察 | prepared/finish 与终态冲突/失败分记录测试；缺完整 HTTP 同步计划失败→合法修订→成功全链 | **PARTIAL**，跨阶段规划/重试属 S25-06/07，当前历史事实不变量已覆盖 |
| TS03 8×8→9×9 更正后旧结果失效 | 参数摘要与 active completed 绑定负例；`superseded` 变迁测试 | **PARTIAL**：单元覆盖，没有真实模型 HTTP 两轮更正读取证据 |
| TS04 FIRST→RESUME 跨轮并能读历史结果 | Qwen FIRST→MCP HTTP Evidence 2/2；既有 S25-05 live 计划 FIRST/RESUME 数据行验收；没有同一真实 S25-03 场景包含 RESUME 后再查 **两步** Evidence/Task history 的证据 | **ACCEPTANCE GAP F1** |
| TS05 已派发无回执的未知结果禁止重复写 | M4 Dispatch unresolved fence、PostgreSQL 崩溃子进程及恢复回归 | **COVERED**（当前 Host 的保守停止；自动核验属 S25-11） |
| TS06 工具注入/输出不得成为授权 | 白名单投影拒绝未知字段；Host 与权限判断独立于 Observation | **COVERED**（代码/自动化的受限合同，不等于对所有恶意提示已测） |
| TS07 Owner/Conversation/Turn 隔离 | owner-scope `get_owned` + Turn 归属验证，HTTP 双用户 404 | **COVERED** |
| TS08 并发版本冲突不得覆盖 | CAS 与 PostgreSQL 并发门禁 | **COVERED**（已测的同库状态/会话边界） |
| TS09 保留未变 Step、废止旧证据 | superseded/参数摘要的单元合同 | **PARTIAL**：真实 HTTP 复杂修订属 S25-07 |
| TS10 partial/failed/cancel 可解释 | partial/interrupted/blocked 与 attempt 错误记录均存在；完整统一终止映射不全归本项 | **PARTIAL**；最终状态解释属 S25-08/14 |
| TS11 敏感/过大安全投影 | `bounded_projection`、名单与最大长度回归，`recall_memory` 仅记录 found | **COVERED WITH LIMIT F2**：`list_tasks.tasks` 与 MCP definition 可保留具体内容，长度限制≠敏感脱敏 |
| TS12 旧 checkpoint 不伪造历史 | NULL 工具版本/legacy 结果兼容测试；`legacy_completion_unlinked` 明示无成功 Attempt 来源 | **COVERED**（旧数据不可回填缺失事实） |

**说明：** 本矩阵的 COVERED 只代表当前 S25-03 应负责的可信状态/证据切片，不表示规划、自动恢复或全系统完成验证已经完成。

## 3. 当前 S25-03 真正的收口门槛

### F1 — 缺少一份「同一任务真实两轮执行完成」的综合证据（验收阻断，非已证实运行时缺陷）

现有真实 Qwen v3 报告仅验证计划创建 → FIRST → MCP Evidence，第二个 `calculate` 步骤有意保持 pending；本项最初的 TS04/Step 5 第 2、10 条要求**跨轮恢复、真实结果以及状态引用一致**。在签署整个 S25-03 CLOSED 之前应补一次 **同一隔离 conversation** 的：

1. 真实 Qwen 计划创建 → FIRST（MCP）→ RESUME（calculate）；
2. 从 DB 验证两个 checkpointed Dispatch 各有不同的 Attempt，绑定正确的 step、arguments_digest、`legacy-plan-v1`；
3. 从 GET `task-state`、`task-history`、两个不同 Turn 的 `evidence` 取回一致结果：MCP `source/definition`、calculate `value=42`，并验证最终 Task/Step 全 completed；其间无重复 Dispatch；
4. 用另一个用户凭相同 cid/turn ID 访问所有证据接口需返回 404（可由现有 HTTP 单独负例补证，真实模型不需要跨用户实测）。
5. 追加「旧 Step 参数更正后旧证据不能支持新 Step」的**确定性主链 API/数据库**负例；若修订入口由后续 S25-07 实现，至少在本项收口中留下可复验的当前保守行为证据，不为赶进度扩建新的规划/重规划模块。

可扩展现有 `evals/s25_03_step6_a_live.py` 增加 **可选** `--full-plan` 场景，但默认旧 2/2 FIRST 验收保持不变。实际 Qwen 报告应不含用户消息/模型正文/个人记忆，保留安全的步骤编号、版本、布尔裁决及错误原因。

### F2 — 有界载荷不能误称已经完成隐私治理（限制，不要求在本轮重构）

`services/evidence_store.py` 的 `_ALLOWED_OUTPUT`：`list_tasks` 可持久化 `tasks`，`mcp_reference_lookup` 可持久化 `source/definition/value`。对个人待办、医疗文档等真实敏感业务内容，按字段长度做截断仍可能落入个人信息。

本阶段处理方式：**明确限定 S25-03 可自动回放内容为可信测试数据或经后续数据治理批准的投影**；真实业务数据需要来源信任、字段敏感分级、保留期/删除及消歧策略，分别对齐 S25-10/12/13/17。禁止将该限制描述为“生产级隐私合规已完成”。本轮不通过删除所有有用历史结果来规避此问题。

## 4. 明确延期而非冒充 S25-03 缺陷

| 事项 | 原因 / 归属 |
| --- | --- |
| `task-history` 只提供最新 100 条及 `has_more`，不能 cursor 浏览所有更早记录 | 确定的 P2 产品查询限制；若产品需要全量回溯，在 S25-17 展示/Trace 服务完善，而不是重做任务存储 |
| 自动核验未知外部副作用、跨节点恢复 | S25-11/12；当前的安全暂停是预期行为 |
| 模型反复选择错误工具、完整多步规划或更正 | S25-01/04/06/07；S25-03 只拥有确定性可信证据层 |
| Task 与 GoalSpec 的全量生命周期、完成目标语义判断 | S25-01/08；不能把 `tool.ok=true` 直接定义为 Goal 完成 |
| 长期任务归档、完整个人数据保留/清除 | 后续治理/隐私需求；本次不能声称已解决 |
| 独立人类 GitHub Approve | 此前 PR 自动化审查和用户授权合并均不等于外部独立代码评审，按团队流程决定是否另需复核 |

## 5. 最终决议与下一步单一工作包

- **实现裁决：** 不新增第二套 Task/Attempt/Observation 状态权威，不再合并两个陈旧 S25-03 分支，不基于旧的“DESIGN REVIEW REQUIRED”描述重写已完成的代码。
- **当前整体状态：`S25-03 = VERIFYING`。** F1 是签署阶段 CLOSED 前唯一必须补齐的**综合验收证据**；F2 为明确范围边界。静态审计**没有证据**表明需要立即整改新的生产 P0/P1 缺陷，但也不能据此断言不存在缺陷。
- **推荐的唯一下一步：** 在独立验收分支上扩展现有受控真实 Qwen 验收为 CREATE→FIRST→RESUME→Task State/History/Two Evidence HTTP，保留真实模型原始错误裁决，不增额外路由/工具权限；CI 验证离线 harness，用户本机完成重复真实运行。通过后归档具体 SHA 和报告、将 `S25-03` 明确标记 CLOSED（限定上述证据层范围），再单独审查进入其他 S2.5 能力。
- 不改 PR #34 已合并内容，不需要回滚；此审查分支仅文档，不执行任何合并操作。
