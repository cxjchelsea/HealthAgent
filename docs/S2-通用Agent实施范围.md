# S2 通用自主 Agent 的实施方案（暂不引入医学）

## 目标
从「用户输入→单次模型回复」升级为「目标→模型决策→受控工具→观察→继续决策→结果」，以任务状态和环境结果作为验证依据。

## S2-A — Agent Loop 与 Prompt
- 引入 LangGraph 与模型原生 Tool Calling。先抽象具版本号的 Core System Prompt、Tool Instructions 和结构化动作 Schema。
- 提供 calculate、read_time（可注入确定性时钟）、模拟任务管理工具；工具白名单和参数校验必须由程序执行。
- 记录每次决策、工具、观察、终止原因。设定最大步数和超时。准备 20 个跨工具任务和失败负例。
- 不允许用关键词路由代替真实模型选择工具。

## S2-B — Context 与长期记忆
- 分离对话历史、Agent task state、长期偏好记忆和结构化事实；建立 consent、scope、来源、有效期与删除机制。
- 比较全历史、摘要及相关记忆检索，测试跨会话记忆和冲突更正；不把一轮历史召回写成长期记忆。

## S2-C — Skills、MCP 与 Harness
- 实现 Skills manifest 和按需加载；MCP Client 接入至少一个受控模拟服务，调用权由 Tool Executor 决定。
- 基于任务限制工具暴露、上下文预算与工作区。重点比较全工具暴露与动态工具选择的效果/成本。

## S2-D — Planning、Replanning 与 Evaluation
- 先 ReAct 基线，再显式计划与局部重规划；工具失败、用户更正或任务变化后保持有效进度。
- 建立可重放轨迹、任务状态终值检查、重复实验、Prompt 版本记录及失败类型标注。
- 是否引入 Subagent 由对照评测决定，而不是预先强制使用。

## 进入 S3 条件
S2 的非领域任务可以实际完成、运行 Trace 可复核，异常和权限负例通过；提供基线与策略比较，不以单纯代码存在作为验收。
