# S2-D — Planning / Replanning / Evaluation：实现与验收

## 第一条正式纵向切片（进行中）

继续沿用唯一 `POST /conversations/{cid}/agent/messages` → `AgentLoop` → `Tool Executor`，不新建独立 Agent 入口。

- `agent/planning.py`：Run 内存里的显式 PlanLedger；最多八步、单一活动 Revision、完成状态与证据工具、Revision 比对与完成进度保留。并不自动将模型宣称的成功写为完成。
- 三项模型可选择的受限工具：`propose_plan`、`revise_plan`、`complete_plan_step`。Schema 严格验证；未知工具 / 未暴露工具仍由 Executor 拒绝。
- 同一 AgentLoop 为多步任务提供规划 Skills Instructions；当工具失败时记录失败观察，模型可以保留已验证步骤并调整待执行计划。
- Agent 结果包含 `plan`、`plan_revision`、`plan_events`，可用于单次运行后审计；历史 `trace` 和 `audit` 沿用已有渠道。
- `evals/s2d_benchmark.py` 在离线可重放 Ledger 场景中对规划、缺证据拒绝、修订、过期 Revision 和终值逐项检查；属于**规则状态机评测**而非真实模型规划质量。
- `tests/test_s2d_planning.py` 验证已完成步骤不会被重规划丢失、非法完成与 stale revision 被拒绝、工具动态暴露范围。

## 必须保留的边界

这是 S2-D 的第一版实现，**不是阶段 CLOSED**：计划仍为单 Run 内存态，尚未跨对话轮次可靠持久化；成功工具观察只证明工具返回 `ok`，无法判定规划步骤语义确实完成；并行调用、时间超限/中断恢复、真实用户更正后的重规划和模型成功率基准仍待完成。

下一步需要真实 Qwen ReAct vs planning 对照，检验模型是否自主提议 / 修订计划，是否存在无意义的空规划和不必要步数增加，并基于多轮任务做持久目标延续。不得以本次状态机 pytest / CI 通过替代真实模型 E2E。

## 复现

```powershell
python -m pytest -q
python -m evals.s2d_benchmark
```

## S2-D 本轮真实 Qwen E2E 反馈修复

用户第一次真实 Qwen Trace：`propose_plan` 成功，`mcp_reference_lookup` 成功，`calculate` 一次失败，`revise_plan` 失败，再次 `calculate` 成功，但在未显示任何 `complete_plan_step` 时宣称已完成。该截图未暴露 revise 的原始错误码，不能反推具体错误原因。

- `revise_plan` 返回可区分的 `plan_not_created`、`stale_plan_revision`（携当前 revision）、`invalid_revision`（携 revision）；Pydantic 参数校验返回受限字段名及错误类型，不回显用户参数值。
- 若模型返回 final 时计划仍有 pending，统一 AgentLoop 将 status 置为 `partial`，在回复前附上明确的未验证步骤告知，不能标记为全部 `completed`。官方 Conversation API 接受并保存 partial 回答，不再误报 503 或丢失用户可读的部分结果；该回复不计入后续已完成消息历史。
- 新增正式 AgentLoop 负例，验证模型虚假完成被隔离、过期 revision 反馈、参数错误可诊断。

**仍待真实 Qwen 本地复验**：需查看后续失败原因与 PlanLedger 输出；Run 内单次 `last_success` 证据尚不能保证步骤语义相关性或多轮持久性。本修复属 S2-D IN PROGRESS，并不声称已有完整恢复链路。

## S2-D 执行证据与计划步骤绑定修复（同一 PR #11）

用户第二次真实 Qwen 验收：工具 RAG 查询和最终计算成功，但未调用 complete_plan_step，Agent 正确返回 partial。该结果验证状态防护，却揭示计划未随真实工具证据更新。

新增**显式验证绑定**：propose_plan/revise_plan 的 steps 在向后兼容字符串步骤基础上可传对象 `{goal,expected_tool,expected_arguments}`。仅受支持的只读工具支持绑定，真实执行观察同时满足 `ok=true`、工具名完全一致、参数精确一致，并且**只有唯一一个 pending 步骤匹配**，才由 PlanLedger 自动记录 completed。没有绑定的旧字符串步骤绝不会被工具成功自动推断完成。相同绑定对应多步骤时拒绝自动完成，避免模糊认领。提示词应引导模型对可验证步骤使用绑定结构。

重规划保留 completed 步骤；修订请求可省略 expected_revision，由可信执行层使用当前 Revision；如显式提供过期值仍予拒绝。失败时附当前 Revision 与重试引导，继续采用模型自行决定是否/如何局部重规划。新增整条 AgentLoop 的模拟决策 E2E：计划→参考查询成功→错误计算→局部修订→正确计算→最终 completed，及失败/歧义证据负例。

**仍待真实 Qwen 复验**：模型是否实际提供结构化绑定并成功调用 `revise_plan` 由本地 Qwen Trace 决定；通过 pytest/CI 不能冒充真实模型成功率。证据绑定是窄域参数匹配，不是通用目标语义验证；跨对话计划持续性和任务变更仍属于 S2-D 后续工作。

## S2-D 增量：真实失败恢复 Harness + 会话计划 Checkpoint

新增 `evals/s2d_live_recovery.py`，使用真实 Provider / 原 AgentLoop 的确定性可复现错误（calculate 8/0），观察模型是否自行 `revise_plan` 并以 calculate 8*8 恢复。该测试必须本地设置 `RUN_S2D_LIVE=1` 才运行，CI 只验自动化，没有用户的 Qwen 服务。与恶意故障注入不同，测试仅使用已允许的算术工具及可验证错误。结果保存于 `evals/results/s2d/qwen-recovery-v1.json`，失败不能掩盖。

跨消息会话计划引入 `conversation_plans` 的持久化迁移 `0004_conversation_plan` 和 `ConversationPlanStore`。正式 Conversation Agent API 仍是唯一入口，读取该会话的 checkpoint，恢复 PlanLedger / revision / 已验证步骤，并在 completed/partial 时保存。对后续用户更正，模型可以调用 `revise_plan` 修改未验证的待执行步骤；已完成步骤按原证据保留。恢复数据是数据库内由后端生成的快照，不允许用户任意传入。已有会话 Owner 校验仍执行。

当前版本的明确限制：首次实现尚未覆盖同一会话**并发消息**对 checkpoint 的竞态写入、用户更改**已经完成**的旧目标时的 supersede 治理、复杂多轮目标归属判断、崩溃前未落盘的 in-flight 状态恢复，以及全链路原子提交。此次开发不宣称这些已完成，也不将本地真实模型测试计为已通过。下一步应优先进行单会话竞态/覆盖保护以及真正用户更正的物理 E2E，之后再考虑 S2-D CLOSED。
