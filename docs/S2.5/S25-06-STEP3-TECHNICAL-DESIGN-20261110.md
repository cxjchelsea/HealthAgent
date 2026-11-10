# S25-06 Step 3 — 版本化依赖规划与串行执行详细技术设计

- 日期：2026-11-10
- 代码基线：`main@9e2e79a473089be47d25d1c10b7a9fa82f06d89b`
- 前置：Step 1 Gap 审计、Step 2 行为契约、Step 2 独立式复审 R1–R7
- 状态：**DESIGN ONLY / NOT IMPLEMENTED / NOT AUTHORIZED FOR STEP 4**
- 架构原则：Primary 是唯一语义提案源；Host 发行授权；既有 M4 Claim/Dispatch/Receipt/Checkpoint CAS 是唯一保存计划工具执行事务边界。不得新建并行 Runtime。

## 1. 方案选择及取舍

**选择：新依赖计划 Schema + 显式 legacy/new Adapter 分发，但共享 HTTP Host、执行 Claim、M4 Journal、Receipt、TaskState。**

不选「直接在旧 `PlanLedger` 上加字段」：旧 restore 和 tool version fence 没有新依赖语义，静默兼容会带来错误执行。不选新 Workflow Engine：会破坏主链一致性、扩大并发和恢复面。也不选将动态参数提前全部渲染为模型生成字符串：缺少可信 Observation 的参数不能在 plan_create 时存在可验证具体值。

最小实施范围：2–8 步、显式依赖 DAG、串行稳定 topo 顺序、一类受信字符串 Observation 引用、首步/继续授权、Checkpoint 恢复、错误及证据联动。无隐式循环、子计划、并行执行、自动重规划或写工具。

## 2. Schema 草案与版本矩阵

### 2.1 顶层 DependencyPlanV1

```json
{
  "plan_schema_version": "dependency-plan-v1",
  "revision": 1,
  "next_step_id": 4,
  "steps": [
    {
      "id": 1, "goal": "查询受信参考定义",
      "status": "pending", "depends_on": [],
      "expected_tool": "mcp_reference_lookup",
      "tool_version": "legacy-plan-v1",
      "argument_bindings": {"term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}}
    },
    {
      "id": 2, "goal": "对前一步定义做字符计数",
      "status": "pending", "depends_on": [1],
      "expected_tool": "text_stats",
      "tool_version": "TBD-versioned-plan-read",
      "argument_bindings": {"text": {"kind": "observation_ref", "source_step_id": 1, "field": "definition"}}
    }
  ]
}
```

**注意：** 上述 `text_stats` + `TBD-versioned-plan-read` 仅是设计示意，**不表示旧版 M4 PlanReadInvoker 已支持它**。如果现有适配器或工具 Schema 不兼容，Step 4 必须先建立独立版本化 read adapter、锁定真实支持矩阵及对应测试；若无法保证安全接入，则首版将动态引用场景限定为既有许可工具之间可以实际通过 Schema 的路径，**不得伪造测试通过**。`plan_schema_version` 不等于 `tool_version`。

### 2.2 版本行为

| 保存快照 | 读取 | 执行 |
| --- | --- | --- |
| 没有 plan_schema_version、旧 step 格式 | legacy parser，保留旧 semantics | 旧 Host + `legacy-plan-v1` 限定路径 |
| 显式 dependency-plan-v1 | strict new parser，不能走 PlanLedger.restore 路径 | 新 dependency selector，仍用原 Host/M4 Claim/Journal/CAS |
| 未知 plan_schema_version | 拒绝 | 禁止 Dispatch |
| 新依赖快照与旧字段混拼/缺关键字段 | 拒绝 | 禁止 Dispatch |
| 历史无工具版本 | 保留 unknown | 仅旧执行器且旧兼容条件允许 |
| 显式 NULL 或混合工具版本 | 拒绝 | 禁止 Dispatch |

### 2.3 工具能力表

计划创建和执行都受严格 allowlist 约束，不以 Registry 暴露等同可用于计划。目前仅 `calculate`、`mcp_reference_lookup` 被 Primary `ProposedStep` 支持；`text_stats` 普通 Registry 支持并不意味着自动成为计划工具。Step 4 首个实现 Slice 应给出 **source schema → target schema** 的确定性实际可用配对；若 `mcp_reference_lookup.definition` → `text_stats.text` 配对需要新 PlanReadInvoker 适配版本，必须先完成 Host/版本、Schema、M4 和回归接线；保持默认关。

## 3. 校验器与错误合同

拟新增只做数据语义的 `agent/dependency_plan_schema.py`：
- `parse_dependency_plan(snapshot)` 拒绝未知或旧格式，验证整体结构/重复 key；
- `validate_graph(steps)`：依赖存在、去重、不可自依赖、严格拓扑有序（所有依赖均前置），拒绝 cycle；
- `validate_bindings(steps, source_message, tool_policy)`：受信 literal 来源证据、observation_ref 只指前置直接或传递依赖、输出字段白名单、目标工具 Schema 的可用性；
- `dependency_ready(plan, committed_receipts)`：仅返回已经满足全部依赖的待执行步骤，不授予执行许可。

稳定错误码建议：`dependency_plan_invalid_schema`、`dependency_unknown_step`、`dependency_cycle`、`dependency_order_invalid`、`dependency_observation_missing`、`dependency_observation_untrusted`、`dependency_result_type_mismatch`、`dependency_tool_version_mismatch`、`dependency_scope_blocked`。错误输出不可含工具原始文本或患者敏感内容。

## 4. Observation 引用与可信读取边界

拟新增 `services/dependency_observation.py`（只读）：
1. 用当前 owner + conversation_id、计划版本/revision、`source_step_id` 定位保存快照和 M4 已 checkpointed Dispatch。
2. 对比具体 `dispatch.id`、`turn_id`、`step_id`、`tool_name`、`tool_version`、`arguments_json` 与 checkpoint 完成事实；确认 `receipt_json.ok = true`，存在同一事务投影的成功 AgentAttempt。若不一致直接拒绝。
3. 从 persisted bounded `receipt_json` 读取 allowlisted 字段。M4 的 `bounded_projection` 对特定工具字段有截断、类型和隐私约束：**禁止把截断的值当成完整原值**；必须在设计/实现中明确字段完整性检测或选择受限制小输入的测试契约。不能把 UI 投影当成任意高可信输入。
4. 经过源输出类型与目标 Pydantic 参数 Schema 二次校验，形成 `ResolvedInvocation`：`step_id`、`tool_name`、`tool_version`、`arguments`、`arguments_digest`、`source_evidence_refs`。此值由 Host 在执行前签定，模型不能生成。
5. 防止跨 Plan Revision 的旧结果引用：只有明确保留且仍兼容的源 Step Evidence 可以使用，修订未审查先拒绝；本期新依赖计划修订先禁用/保守阻塞，除非新版本设计验证全套失效规则。

**数据设计风险：** 当前 M4 先保存工具 Receipt，后在完成/提交事务中 `checkpoint_in_session` 改为 checkpointed；新增的依赖解析必须发生在此前完成后的独立受信读取边界，不能仅靠执行循环内临时 `result`。

## 5. 两阶段 Host 与单步真实执行

A. 创建时：Primary 模型提案 → quote/source binding → 新 Schema 和 DAG 校验 → Host 对所有潜在步骤做许可审查 → 原子保存新依赖计划；**零工具 Dispatch**。

B. 用户首步或恢复请求：Primary → Host 决定保存计划控制 → 读取 owner-scoped 原始 snapshot → 检查 plan_schema_version，选择 new/legacy parser；新 Parser 只生成可信模板信息，不通过旧 `inspect_plan_invocations` 对未解析参数强行生成 digest。

C. 获取原 M4 Claim：绑定当前 Turn/Request/原始 snapshot、plan revision/version；若失败，零派发。

D. 单步循环：从持久化认可的状态选择就绪 Step；仅在 step 的全部前置成功 checkpointed 后读取其 Observation 引用；解析成具体参数，再完成 Host schema/grant/version/args digest 及执行预算审查；形成单步 trusted binding；执行 Guard → M4 begin → 实际工具 → bounded receipt。失败停止。

E. 完成提交：用明确 step_id + dispatch_id + tool + 参数 digest 绑定当前 Step，而非旧 `PlanLedger.observe(name,args)` 猜测唯一归属；CAS 把 Checkpoint 和有关 M4 dispatch 同事务 finalize；S25-03 Projection 只从 checkpointed receipt/真实 Attempt 出发。之后才能计算下一步是否 ready。

F. 最终返回：区分所有步骤的执行状态和当前缺口；不把 `ok` 解释为 S25-08 用户目标已被充分满足。

**关键接口变更：** 目前 `PlanReadInvoker` 自身不负责 Claim 或 Checkpoint；新方案同样不得让 Adapter 自己发行授权。设计时须画出完整事务序列并在 Step 4 自动化里验证“上游 checkpointed 才可执行下游”。这里是**需实施的新逻辑**，非现有能力。

## 6. 首步、继续和失败状态

- `execute_first`：只考虑创建时拓扑有序列表中的首个 pending 步骤，满足依赖时**最多一次**受信工具调用；无 ready 则返回 blocked，不补做其他步骤。
- `resume`：按稳定 step 顺序逐一执行，任一步失败/无证据/Unknown Dispatch/Scope Conflict 即停止；不得跳跃执行其他独立步骤而隐式扩大范围。
- `ready` 为由 Checkpoint/Receipt 计算的只读投影，非永久可伪造权限；`running` 对应 M4 Claim/Dispatch；`completed` 由 checkpointed receipt 绑定；`blocked/failed/interrupted` 由可审计错误投影表示。不要强迫旧 `PlanLedger.restore` 接收这些新字符串。
- 失败、崩溃及中途取消的任何恢复先执行已存在的 M4 reconciliation；不得自动重放未知实际结果，保持外部作用不等于 exactly-once 的表述。
- 计划修订（S25-07 主要范围）在首版新格式中保守拒绝，除非完成独立迁移及图失效设计；原 legacy plan_revise 不受影响。

## 7. 建议文件影响及 Slice 划分

| Slice | 新增/修改（拟议） | 退出门禁 |
| --- | --- | --- |
| A — contract only | `agent/dependency_plan_schema.py` + 全面 Schema/DAG 单测 | 非法计划一律零 Dispatch，旧 plan 未受影响 |
| B — read-only evidence resolver | `services/dependency_observation.py` + SQLite/Postgres 受信 Receipt 对照测试 | owner/revision/version/字段/截断等边界 fail closed |
| C — saved-plan Host admission | `agent/primary_route_v1.py`、Host binding/计划创建接线、必要 schema/版本特性开关 | plan_create 零派发；legacy 真实测试全部稳定 |
| D — guarded serial execution | `agent/loop.py` 或同一 loop 内调用的纯 selector/runner；`services/plan_store.py`、`services/plan_dispatch.py`、S25-03 projection 最小兼容补齐 | first/resume、M4 Claim/CAS、crash/replay 物理通过 |
| E — integration/eval | `tests/test_s25_06_*.py`、`evals/s25_06_*.py`、验收文档 | 本地 Qwen 多措辞 E2E，代码审查且精确 HEAD CI 绿 |

**Slice 不等同额外分支/PR**：优先一个 S25-06 实现分支渐进推进；若 B/C/D 扩张到破坏旧合同，先停下独立复审再实现。

## 8. 版本、迁移、回滚

- 仅新增显式 plan 格式入口；默认 feature flag 关闭。旧记录不能因上线获得新语义。
- 若实现涉及数据库增加 Evidence Ref 或类型字段，先出 Alembic 迁移和 PostgreSQL rollout/backfill/rollback 合同；不在无测试的情况下修改现有非空约束。
- 未启用新 flag：新计划创建和执行均拒绝，不创建空计划或绕到 legacy。
- 新功能灰度时必须有真实 Host/执行器和证据可观测性，flag off 不影响原 `legacy-plan-v1`。
- 回滚后：已存在的新格式 checkpoint 只读展示/提示升级，不交由旧执行器执行。

## 9. Step 4 之前的硬门禁

1. 独立核验旧版工具执行支持范围、M4 receipt 的真正字段完整性；确认一条可运行的动态引用 source/target 配对，避免把草案示例当现成工具。
2. 审定新版本保存计划的完整 JSON Schema + allowed state projection；明确是否需要数据库迁移及来源 ID 持久化。
3. 画出 Host admission→M4 Claim→step resolution→Dispatch→Receipt→Checkpoint CAS/Projection 的事务图，并列出每个异常后安全状态。
4. 提供旧 snapshot 与新 schema 的反向兼容/反例，重复 step 名称+参数不得误绑定。
5. 制定至少 10 条确定性、4 条正式 HTTP、PostgreSQL crash/worker/concurrency、真实 Qwen ≥2 种任务×2 次的验收矩阵。
6. S25-06 不得默认开启临床工具、写工具或外部不受信 MCP；若首版 read-only source/target 无可行闭环，宁可缩小 scope 也不能绕过 Host。
7. 本文件属于详细设计，不是“Step 3 已通过独立复审”，需要另行审批后进入 Step 4。
