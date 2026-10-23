# D03-F01 — Plan-only Execution Gate / Stepwise Resume Acceptance (2026-10-23)

**PR**: #22，Draft；main 未修改。

## 人工复现（真实 Qwen）

用户第一轮要求“请建立一个包含两步的执行计划：第一步查询 MCP 中 RAG 的定义，第二步计算 8*8”。Agent 未调用 `propose_plan`，直接对业务工具 `mcp_reference_lookup` 和 `calculate` 执行；`status=completed`，`plan=[]`，`plan_revision=0`，`plan_events=[]`，`plan_checkpoint=null`，`execution_mode=read_only`。明确为 **D03-F01 CONFIRMED FAIL**。后续所谓暂停/恢复无可信计划可依据。

## 本轮修复

- 新增 `agent/plan_intent.py`：独立、受 Schema 约束的规划意图分类（`plan_only / execute_first / resume / other`）。模型生成计划步骤时，Host 严格限定为当前已支持的只读 `calculate`、`mcp_reference_lookup`；参数必须来自本轮用户原始指令，经过原有 Pydantic Schema 核验。没有用户参数来源、工具不允许、计划不完整时返回 `partial/plan_requires_clarification`，**不执行业务工具**；模型不能扩大工具权限。
- 在 `apps/api/main.py` 正式结构化 API 中，把 `plan_only` 绑定步骤提交给 `PlanLedger.propose`，将它的 `checkpoint` 通过既有 PlanStore 原子存储，返回 `plan_created_pending_execution`，所有步骤起初为 `pending`；有计划时拒绝盲目覆写。
- 对明确的 `execute_first`、`resume`，先检查真实已保存计划和独立 `execution_mode`。如果策略为 `respond_only`，明确拒绝执行；对已验证、只读、带工具参数绑定的已有计划才调用 `AgentLoop.run(plan_control=...)`。第一步只执行一步并返回 `partial`；继续只执行剩余待完成步骤。真正工具 Observation 决定 `completed`，不根据模型文字伪造完成，也不重跑已完成步骤。
- 其他普通消息保留原本的 AgentLoop 行为，避免把一切只读工具请求都误视为“只规划”。`read_only` 工具模式从来不等价于当前用户授权立即执行计划；本次补的是时机/目标门禁。
- **已知界限**：`plan_only` 仍由真实模型结构化分类，存在分类不稳定风险；绑定目前仅支持计算和 MCP 参考检索两类带可从原文验证的参数，不能将其宣传为全领域计划调度或完整 S25-03 的授权/证据系统。新增模型分类意味着额外一次请求开销。当前自动化测试使用受控分类，不等于真实 Qwen 已稳定通过。

## 自动化测试 / 人工复测

`tests/test_s25_02_d03_plan_gate_api.py`：完整 Conversation API + SQLite 的“只建两步计划（零业务调用）→ 执行第一步、第二步 pending → 执行剩余、不重复第一步”回归，以及 Shell/无原文证据的计划绑定拒绝。原有安全用例与综合回归继续保留。

最新代码 CI：<https://github.com/cxjchelsea/HealthAgent/actions/runs/39884855560> **SUCCESS / 222 passed / 3 skipped**，code HEAD `8be8182ebf44b7e9e22605c3f75d04ceed992a95`。

人工使用**完全相同**的第一轮提示词，要求 `status=partial`、`termination_reason=plan_created_pending_execution`、`plan_revision=1`、两步 `pending`，无 `mcp_reference_lookup/calculate` 执行 Trace。第二轮指定只执行第一步，看到真实 MCP 工具成功、第一步 completed 第二步 pending。第三轮只执行剩余步骤，验证 calculate 8*8=64、计划全 completed、不会重复第一步。记录三轮 `trace/plan/plan_checkpoint/execution_mode/status`。人工未通过前 D03 保留 RETEST REQUIRED。
