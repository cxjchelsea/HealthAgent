# S0/S1 交付与验收记录

2026-10-19 — 对照 V4.2 阶段要求。

## 已编写并实际测试
- FastAPI 应用、`/healthz`、`/readyz`、`/model`、会话创建/列表/历史接口。
- SQLite 本地持久化；Compose 中声明 PostgreSQL 16，需本机联合验证。
- Fake Provider 与 OpenAI-compatible Chat Completions 流式适配。
- SSE `started/message_delta/usage/message_done/error`，错误不计为完成历史。
- 多轮上下文传递、会话身份隔离（模拟 token）、客户端请求 ID 去重。
- 单页聊天演示、前端停止按钮（基于取消 fetch）、配置样例。
- GitHub Actions 执行 pytest、确定性 eval、compileall。
- 确定性 smoke 评测 5 条样例，非真实 Agent 能力测评。

## 当前状态
- S0: 本地可运行基线已建立；Compose 的实际容器启动和 PostgreSQL 联合验证待完成。
- S1: 部分实现且单元/接口测试通过；正式阶段验收尚未完成。

## 未完成门槛
- 真实供应商连续五轮对话与至少十条运行证据。
- 稳定的后端主动取消机制与断连/超时恢复验证。
- 结构化 goal/intent 理解与歧义澄清任务评价。
- 官方 usage Token 度量、生产鉴权、数据库迁移、并发冲突治理。
- Lint/format/typecheck 完整门禁和正式依赖锁定。

上述未完成项不得被解释为已通过。后续 S2 仍未开始。
