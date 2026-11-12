# S25-06 Step 1 — 多步骤规划与执行现状基线和缺口审计

- 日期：2026-11-10
- 仓库：cxjchelsea/HealthAgent
- 基线：`main@9e2e79a473089be47d25d1c10b7a9fa82f06d89b`（PR #34 已合并）
- 范围：静态代码审计、已提交测试合同审查、Step 2 前置验收方案
- 状态：**STATIC REVIEW COMPLETED / PHYSICAL REGRESSION NOT RUN**。本文件不声称在当前环境运行 pytest、HTTP、PostgreSQL 或真实 Qwen。
- 本轮仅增加文档，不修改默认路由、Host 授权、M4 调度、工具版本或数据库。

## 1. 目标与判定标准

依照 `docs/S2.5-通用自主Agent能力补齐开发策略.md`，S25-06 的主题是「多步骤规划与执行」：任务分解、步骤依赖、执行顺序、状态转换；保留 S25-07 Observation 触发重规划和 S25-08 结果完成验证的独立边界。目标是在**正式 Conversation API + 唯一 Agent 主链**内，基于已核实工具 Observation，安全执行可解释的多步骤任务。S25-06 不建立第二套 Runtime，不凭模型提案发行执行授权，不将 `ok=true` 自动等同用户目标达成。

## 2. 当前代码和调用边界

| 组件 | 文件与实现证据 | 审计判断 |
| --- | --- | --- |
| 正式 HTTP 入口 | `apps/api/main.py` 的正式 `POST /conversations/{cid}/agent/messages` 路由；计划创建 `build_plan_checkpoint`，执行计划将 `plan_control` 等交给 AgentLoop | 已有入口，禁止 06 另立写入口 |
| 主语义提案 | `agent/primary_route_v1.py`：`plan_create/plan_execute_first/plan_resume`；`ProposedStep` 仅允许 `calculate` 或 `mcp_reference_lookup`；创建计划要求 2–8 个有引用证据的步骤 | 模型可生成有序可执行步骤，但目前不是通用依赖计划 |
| 计划绑定 | `agent/plan_intent.py` 的 `build_plan_checkpoint`：步骤参数必须锚定当前请求，校验实际工具 Schema；`agent/planning.py` 的 `PlanLedger` 最多 8 步 | 有严格的安全计划创建合同，不能为了自由规划直接松绑 |
| 持久化与并发 | `services/plan_store.py`：按会话保存 snapshot；ExecutionClaim 和 snapshot CAS；执行者 Turn 绑定 | 复用且严禁绕过 |
| 调度和工具调用 | `agent/loop.py` 的 saved-plan 分支：恢复 `PlanLedger`，筛选 `pending`，`execute_first` 截取第一步，`resume` 按列表顺序迭代；每步由 `PlanReadInvoker` 调用 | 当前为线性 pending 顺序，未根据依赖 DAG 排序 |
| 工具版本与 Host | `agent/host_plan_execution_binding.py`、`agent/plan_invocation.py`、`agent/plan_read_invoker.py`、`agent/plan_tool_versions.py` | 执行前重新核查授权、参数、版本；PlanReadInvoker 与普通 Registry Invoker 不可混同 |
| 持久化执行证据 | `services/plan_dispatch.py`：Dispatch `dispatch_unknown` → Receipt `observed/observed_failure` → checkpointed；`services/task_state.py` 将 Task/Step 状态与 Attempt 关联 | 06 应保留 M4 journal/receipt/CAS 提交顺序；不承诺外部 exactly-once |
| 失败与部分恢复 | `agent/loop.py`：失败停止后续步骤；`agent/plan_recovery.py` 只提供受限计算候选；有 journal 时不能对失败工具直接执行第二次无日志恢复 | 有失败防护，不是通用依赖失败处理和重规划 |

## 3. 静态审计发现（与目标的差距）

**G06-01 / P1：缺少显式依赖图。** `PlanLedger._step` 与 `PlanLedger.restore` 只持久化 step ID、goal、pending/completed、工具及参数等信息；未建立 `depends_on` 契约、无环检查、ready 集合、依赖跳过/阻塞的统一规则。现有 `resume` 是按 `pending` 列表顺序执行。

**G06-02 / P1：跨步骤数据绑定缺失。** 计划创建要求每步参数直接来源于用户当前请求，不支持未来步骤从前序可信 Observation 的允许字段动态解析输入；因此暂不支持「先查询 X，再根据查询结果计算 Y」这样的可验证数据依赖。不可让模型以自由字符串替换绑定，必须由 Host 校验类型、来源、允许字段和最大大小。

**G06-03 / P1：步骤生命周期不完整。** `PlanLedger` 自身只有 `pending/completed` 作为持久 step status；TaskState 有 task-level `partial/blocked/interrupted/waiting_user` 和追加转换日志，但未形成与依赖调度一致的 `ready/blocked/failed` 步骤状态合同。不能简单重写旧 checkpoint 并破坏旧版恢复。

**G06-04 / P1：现有创建提案的工具覆盖窄于 Registry。** `ProposedStep` 仅支持 `calculate` 与 `mcp_reference_lookup`；PlanReadInvoker 绑定 `legacy-plan-v1`，而普通读工具 Registry 使用 `1.0.0`。06 不能直接把所有 Registry 工具扩展成可保存执行步骤；这需要单独授权和版本兼容设计。

**G06-05 / P1：执行授权作用域和依赖调度尚未联合建模。** 当前 `execute_first` 表达原计划的第一待执行步骤；若未来变为依赖 ready 队列，需精确定义“第一步”与用户授权范围，避免自动执行额外步骤、隐式重试或越权继续。

**G06-06 / P2：计划质量的真实模型泛化尚无 06 专项证据。** 已有 05 真实 Qwen 本地 26/26 是工具治理及保存线性计划验收，不代表依赖型多步骤任务成功。06 需要固定任务、多样自然语言、失败/恢复和可核验真实工具 Observation 的 E2E。

**G06-07 / P2：并行调度与真正重规划。** 当前没有可证明的通用 DAG 并发执行。建议本期先做依赖图 + 串行拓扑调度；局部重规划属于 S25-07，最终交付证明属于 S25-08。并发先作为有证据收益后的后续选项。

## 4. 建议的 Step 2 设计约束（尚不是最终接口定稿）

1. 保持单一 Primary → Host → AgentLoop → Guarded M4 PlanReadInvoker → Receipt → Checkpoint CAS 主链；不创建备用 Executor。
2. 将旧 `legacy-plan-v1` checkpoint 当作明确版本的兼容格式：静态线性顺序解释，拒绝静默升级为新依赖语义。新增依赖字段必须采用版本隔离及迁移/拒绝策略，历史 NULL 版本不得假定为最新。
3. 规划器产出受限的 Goal/Step 依赖提案，Host 独立校验 DAG 无环、step IDs 存在、工具权限、参数 Schema、观察字段绑定和任务范围。模型不能制造已完成状态、Attempt 或授权。
4. 实现**串行** ready-set 调度：只有依赖的可信结果已经持久化后才允许下一步；对读取不到 Observation 的情况 fail closed，不把工具 `ok=true` 当成最终目标证明。
5. 将中断、取消、目标变动和 CAS 冲突与现有 Turn/Claim/Pending 规则对齐。失败步骤保留可审计原因，不触发无授权自动补偿或并行重试。
6. 只做最小可验证的 Observation 输出引用；如何支持动态探索和替代计划属于 S25-07。不要为通过特定中文表达增加关键词路由。
7. 对普通 Registry 读取能力是否加入计划，提出**逐项准入表**并在设计批准后实施，避免一次性开放不受限制的工具。

## 5. Step 1 物理验收建议与命令（尚未运行）

以最新 main 或对应 Step 1 分支工作区，先人工确认 `git status --short` 与 SHA，再运行：

```powershell
cd D:\HealthAgent
git fetch origin
git status --short
git rev-parse HEAD
.\.venv\Scripts\Activate.ps1
python -m pytest -q tests/test_s2d_planning.py tests/test_s25_02_m4_dispatch_journal.py tests/test_s25_03_step6_a_evidence.py tests/test_s25_05_m4_invoker_migration_gate.py
python -m pytest -q
```

以上测试仅证明已覆盖的线性计划、M4、证据与版本行为。需要补充独立 PostgreSQL 物理、真实模型 HTTP 的执行证据，**不允许把现有 CI 或 fake/simulated 结果记作 06 真机验收**。注意数据库、Provider URL 与凭据由本地环境提供，禁止泄漏到测试报告。

## 6. Step 2 行为验收矩阵（设计输入，不代表已实现）

| ID | 验收行为 | 判定 |
| --- | --- | --- |
| AC06-01 | 模型分解陌生的多步骤目标，输出有限、有依据的步骤与依赖 | 无硬编码固定任务序列 |
| AC06-02 | 拒绝未知依赖 ID、自依赖、循环和越界步骤 | 无 Dispatch、明确错误 |
| AC06-03 | A/B 前序成功后才执行依赖 C，非依赖步骤可以独立就绪 | 正确的逐步顺序与证据 |
| AC06-04 | B 的参数引用 A 的已持久化、已验证 Observation 允许字段 | 值、来源、版本与类型一致 |
| AC06-05 | A 失败则依赖 C 不执行；不依赖的步骤遵守获准执行范围 | 无伪完成、无越权 Dispatch |
| AC06-06 | 执行中断后恢复，已 checkpointed 步骤不重新执行 | Claim、Receipt、CAS 一致 |
| AC06-07 | execute_first 只执行用户批准范围内的一步；resume 不扩大工具权限 | 真实 HTTP + Dispatch 数目核验 |
| AC06-08 | 老版本保存计划保持旧语义，未知版本 fail closed | 旧数据兼容及隔离验证 |
| AC06-09 | PostgreSQL 并发 Claim、异常 Dispatch 和崩溃恢复 | 与 M4 安全不变量一致 |
| AC06-10 | 真实 Qwen 结合实际工具 Observation 完成至少两类依赖型任务 | Trace、Attempt、Receipt 与最终状态一致 |

## 7. 审计后下一动作与门禁

- **本次完成：** 对最新 main 的代码结构、契约、主要 Gap 进行静态审计；形成初始验收矩阵。
- **尚未完成：** 本地定向测试/全量 pytest、PostgreSQL 物理验收、06 专项真实模型 E2E；不能把这些列为 PASS。
- **Step 2 推荐输出：** S25-06 需求与行为合同（依赖边语义、Observation 引用协议、失败传播、first/resume 权限、版本兼容矩阵）及与 S25-07/08 的明确职责切面。
- **禁止事项：** 未经审查不得改变 Host 默认授权、M4 Dispatch 原子顺序、计划版本，不得直接合并文档或在 main 上试写生产功能。
