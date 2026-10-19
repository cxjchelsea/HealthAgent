# S2-A 第二轮集中验收

## 证据分级
1. **离线工程证据**：GitHub CI pytest、20-case scripted harness、失败观察→纠正、鉴权与 PostgreSQL 门禁；证明实现可执行，不证明 Qwen 任务准确率。
2. **真实模型证据**：用户曾在 Windows .venv 使用 Qwen3:14b 完成原生 calculate tool_call 1/1（2.01 秒）。这是最小能力证明，不能外推为 20 个任务完成。
3. **待执行真实 Benchmark**：`RUN_S2A_LIVE=1 python -m evals.s2a_benchmark --real`；产出 `evals/results/s2a/real.json`，记录每条任务的工具序列、完成状态、步骤、耗时及 Prompt 版本，不存明文模型对话。真实评测失败不自动按通过处理。

## 注意事项
- 20-case 数据集中既有单工具、多工具，也有错误计算和非法工具请求。
- 这个指标仅是严格工具序列匹配，任务成功还需人工审核语义、异常策略和安全。
- 当前任务列表是 run-local 模拟数据；跨次调用后不保留。
- 暂不支持流式 Agent UI、长期记忆、持久任务、生产认证。
- 当前 Prompt 不保证复杂任务稳定成功，须根据真实评测调整。
