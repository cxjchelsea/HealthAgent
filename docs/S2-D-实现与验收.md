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
