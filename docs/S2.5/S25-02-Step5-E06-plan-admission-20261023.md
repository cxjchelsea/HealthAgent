# S25-02 Step5 E06-F01 — 普通任务被误判为正式执行计划（2026-10-23）

## 复现
真实 Qwen3:14b 在网页新会话中，输入“帮我拟定三项明天的工作任务，只给出草案，不要创建任务，也不要保存。”与“现在请正式创建一个任务，名称是 E06权限测试任务。”均得到 `partial/plan_requires_clarification`、`plan_binding_missing`，没有正常经过普通响应/任务工具决策。不能据此证明越权写入；但说明普通请求被错误接入 PlanLedger。

## 根因
计划控制函数 `review_plan_intent` 的 `plan_only` 是一次模型分类判断，不足以作为强制进入正式可执行计划的授权证据。此前 API 在 `build_plan_checkpoint` 校验失败时总返回 `plan_requires_clarification`，将**普通文字任务草案**与**单次创建任务**都当作“可绑定的多步正式执行计划”，阻断普通 AgentLoop。

## 本轮架构边界调整
`apps/api/main.py` 将正式计划的准入依据改为 **模型请求 + Host 可验证的、源自当前用户内容的计划绑定**。只有 `build_plan_checkpoint` 校验成功时才创建 PlanLedger 检查点，获得 `plan_created_pending_execution`；模型虽然声称 `plan_only` 但无可验证的步骤、工具/参数或原文来源时，不得作为有效计划控制流强行澄清/阻断，而是继续已有普通 Understanding 与 Execution Policy，由 AgentLoop 按原权限处理。原先 `read_only/respond_only/execute` 权限边界未放宽，也没有为“草案”“创建任务”等输入增加关键字分流规则。

这次修复针对**错误的控制流准入**，不是替代模型对任务目标或写入授权的理解。普通任务创建能否成功，还需要真实 Qwen 以及执行权限测试；普通草案即使不落入 PlanLedger，也必须验证没有调用 create_task/save_memory。正式计划如果无法绑定参数，也不会形成有副作用的计划，必要时会由普通模型澄清具体细节；不再把所有这种情况武断归入 PlanLedger 的 `plan_requires_clarification`。

## 回归范围
`tests/test_s25_02_e06_plan_admission.py` 模拟错误 `plan_only` 分类，确保普通草案和单项任务请求继续进入常规 AgentLoop，响应不再是 `plan_requires_clarification`，无计划检查点；验证有效的 RAG + 8*8 正式计划仍正常创建。完整 D03/D05/D06 回归需要 CI 支持。人工必须在同会话验证：
1. 草案请求仅产生草案，不调用写工具。
2. 明确请求创建 E06权限测试任务，依据策略实际创建且仅一次。
3. 查看两轮 `trace`、`execution_mode`、任务存储结果，特别关注此前反复的 `goal_clarification` 稳定性问题。

PR #22 Draft，未获用户授权不得合并。
