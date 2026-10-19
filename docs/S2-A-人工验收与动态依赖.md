# S2-A 最后工作包：动态依赖 E2E + 人工验收台

## 测试场景
目标：先获取实时 UTC 时间，再创建标题为 `记录 UTC HH:MM` 的任务，HH:MM 必须取自第一步工具观察结果，不能由用户预先提供。

`tests/test_s2a_dynamic_dependency.py`：
- 离线确定性测试注入固定时钟 09:42，验证完整 LangGraph Observation → Decision → Tool Args。
- `RUN_S2A_LIVE=1 python -m pytest tests/test_s2a_dynamic_dependency.py -m live -v` 用真实 Qwen，检验工具调用顺序、不同模型决策轮次、返回时间与任务标题完全一致、任务状态正确。
- 离线 CI 通过不等于真实 Qwen 已通过。不能用 20 条旧 Benchmark 替代此测试。

## 人工测试
启动 `uvicorn apps.api.main:app --reload`，打开 `http://127.0.0.1:8000/`。
- S1 普通聊天：旧会话历史、SSE 与停止生成照旧。
- S2-A Agent：点击“Agent S2-A”切换，输入目标；查看模型最终回答、决策与工具调用 Trace、状态和耗时。
- Agent 是一次 Run，不跨消息保留任务和对话上下文；只有最终 JSON 返回，目前不是服务端事件流。
- 点击“停止等待”只中断网页等待，不承诺取消服务端运行。避免声称已有主动执行取消。
- Trace 仅提供脱敏名称和状态，不展示工具参数/原始 Observation。

人工验收建议：直接回答、计算、UTC 时间、创建并列出任务、非法工具、计算错误、动态依赖时间任务；核对 S1 入口功能没有退化。
