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


## D03-F02 — 计划续接被错误拒绝（2026-10-23）

真实 Qwen D03 三轮复测：第一轮只建计划、未执行，第二轮 MCP 第一步成功、第二步 pending，第三轮明确说“请继续完成刚才计划中剩余的步骤，不要重复第一步”，但 Execution Policy 把该请求分类为 `respond_only`。此前分支按设计拒绝，返回 `partial/execution_not_authorized` 和 `plan_execution_denied`，导致第二步不能继续。这是**策略复核不充分**，不是允许无条件绕过 `respond_only` 的理由。

新增 `agent/plan_execution_review.py`：仅在用户当前请求已被 `review_plan_intent` 判为 `execute_first` 或 `resume`，且独立 Execution Policy 为 `respond_only`，又确实存在从该会话 PlanStore 读取的可信计划检查点时，才启动受限的 `PlanExecutionReview`。复核的上下文包含当前原文、待执行步骤数量和目标，而不是把旧计划本身当作最新执行授权。复核只回答“用户本轮是否明确授权执行待完成步骤”。必须先通过 `PlanLedger.restore` 的结构检查，且所有待执行步骤必须是带参数绑定的允许只读工具。复核确认为真时，仅将本轮模式收缩到 `read_only/tool_action` 并交给既有步骤恢复执行；假、格式非法、模型不可用、无检查点、步骤绑定不可信时都继续拒绝。绝不授予 `execute` 或开放其他工具。

`agent/loop.py` 的直接计划恢复路径同步返回实际 `execution_mode`，方便人工审计权限来源。新增正式 Conversation API + SQLite 三轮回归：模拟第三轮 Execution Policy 误判 `respond_only` → 有证据的独立复核 → 只执行剩余第二步，PlanLedger 全部 completed，Trace 不重复第一步；另有复核拒绝时保持 `execution_not_authorized` 且零工具调用的负例。

代码提交 `a971cd56faa41186d4e3f3a69447421788c44d78`；CI <https://github.com/cxjchelsea/HealthAgent/actions/runs/39885878065>：SUCCESS，224 passed / 3 skipped。本机制的独立复核仍是概率性模型判断，不能证明真实 Qwen 稳定百分百通过。必须在相同对话、相同第三轮用户原文中进行真实模型复测，核查 `status=completed`、`execution_mode=read_only`、`calculate` 执行结果 64、`plan` 两步 completed、无重复 MCP，方可正式关闭 D03-F02。


## D03-F03 — 已完成计划再次继续时缺少终态保护（2026-10-23）

真实 Qwen 前三轮人工测试：创建计划未执行、第一步 MCP 已验证完成且第二步 pending、第三轮只执行 calculate 8*8=64，计划两步都 completed。用户在第四轮再次输入“请继续完成刚才计划中剩余的步骤，不要重复第一步”，页面再次生成计划执行结果，截图中显示“执行步骤：3 条”。第四轮无 Network Trace，故不能证明发生了真实重复工具调用，但从代码可确认终态缺口：`AgentLoop` 已有恢复分支必须存在 pending 步骤，全部 completed 时请求会落入普通工具决策链，存在重复执行和重复宣称风险。

**修复**：`apps/api/main.py` 在结构化 `PlanRequest.intent` 为 `execute_first` 或 `resume` 时，首先用 `PlanLedger.restore` 验证**当前会话持久化检查点**。当存在已保存计划且全部步骤均为 `completed` 时，直接返回 `status=completed`、`termination_reason=plan_already_completed`、`trace=[{step:0,type:plan_already_completed}]`、`steps=0`，保留计划 `snapshot/revision/checkpoint`，响应明确告知无剩余步骤。本轮不调用执行授权复核、不进入 AgentLoop、不调用 MCP/计算或其他工具。只有非终态计划才进入已有执行权限复核和计划恢复路径。快照无法验证时不得按已完成计划放行。

**回归**：扩展 `test_d03_f02_misrouted_respond_only_can_resume_verified_pending_plan` 为四轮 Conversation API + SQLite 测试。模拟第四轮再次请求 resume，断言 `plan_already_completed`、零执行步骤、原 checkpoint/revision/plan 不变、不会再次调用 plan execution review。此前 B03/C01～C05、D03 第一到三轮与拒绝分支继续回归。

代码 HEAD `4e16b8c9d74da15d0e7ea960ba52a42145fbe62c` CI <https://github.com/cxjchelsea/HealthAgent/actions/runs/39886911127> SUCCESS（224 passed / 3 skipped）。该结果为自动化通过，尚须真实 Qwen 第四轮重测，确认仍不发生业务工具调用后方可关闭 D03-F03。PR #22 Draft、main 不变。
