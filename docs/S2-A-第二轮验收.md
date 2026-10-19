# S2-A 第二轮针对性加固：证据判分 v2

上一份 real.json 的 20/20 是工具序列匹配，不是严格任务成功率。它无法核验参数、最终模拟任务状态和观察后再决策。

本次新增：

- 仅 `AgentLoop.run(..., include_evidence=True)` 提供运行内审计：工具参数、观察及任务终态。普通 /agent/runs 不含这类敏感数据。
- `grade` 强制检查表达式、数值、任务标题及任务状态、list_tasks 返回、失败观察与失败说明。
- S2A-011/012/015/016/017 要求第二工具处于更晚的模型决策步骤；同批批量工具调用不能作为观察后继续决策的证据。
- S2A-013/014 的任务标题已经包含数值，所以不强行认定模型必须观察上一工具才可继续。
- 反例测试证明：错误标题、同批工具、失败后谎称成功都会失败。
- JSON 报告仅保存每项检查的真假，不持久化原始消息、工具参数或结果。

运行方式：

```powershell
python -m pytest -q
python -m evals.s2a_benchmark
$env:RUN_S2A_LIVE = "1"
python -m evals.s2a_benchmark --real
Remove-Item Env:RUN_S2A_LIVE
```

真实结果输出 `evals/results/s2a/real-v2.json`。原来的 real.json 保留作为 v1 历史证据，不得重标为 v2 通过。

局限：最终自然语言回答目前只做部分受限文本断言；不能把这些检查宣称为通用语义评测。模型可一次决策请求独立工具调用，但依赖性任务必须分轮观察。
