# HealthAgent — 统一会话 Agent（S2-B 第一版已收口）

HealthAgent 是渐进式开发的通用自主 Agent 项目，使用 FastAPI、LangGraph、模型原生 Tool Calling、SQLAlchemy/Alembic。**唯一正式 Agent 消息入口**为 `POST /conversations/{cid}/agent/messages`。S2-A Tool Loop 与 S2-B Context / Task / Memory / Offline Evaluation 已合并至 `main`；下一阶段是 S2-C Skills/MCP/Harness。当前不提供医学问诊服务。

## 当前可运行能力

- Agent 可直接回复或在有界循环中自主选择白名单工具：`calculate`、`utc_time`、`create_task`、`list_tasks`、`save_memory`、`recall_memory`；工具校验由程序执行。
- 会话消息与 Turn 持久化，按已完成消息和消息数量/字符预算构建上下文；网页支持新建、恢复会话及单次执行 Trace 查看。
- 任务写入 `conversation_tasks`，**同会话**跨轮、重启保留；长期键值记忆存入 `user_memories`，由**同一用户**跨会话访问。Agent 写入长期记忆需当前请求 `memory_write_consent=true`，网页有相应勾选框。
- 已认证 `GET /memories`、`PUT /memories/{key}`、`DELETE /memories/{key}` 用于审查/更正/删除本人记忆。
- 离线 S2-B5 评测调用**正式会话 API**，提供 11 条 deterministic scripted fixture 场景。它验证工程链接，**不是 Qwen 真实模型准确率**。

## 主要目录

`agent/` 决策循环、Tool Schema、Context Builder、供应商适配；`apps/api/main.py` 单入口 API；`apps/web/index.html` 无构建网页；`services/` 会话任务与用户记忆持久化；`alembic/versions/` 版本化迁移 `0001_s0_s1` → `0002_tasks` → `0003_memory`；`tests/`、`evals/` 回归及离线评测；`docs/` 设计、实施路线与阶段收口文档。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e '.[dev]'
# 基于 .env.example 创建 .env，并在本机设置真实模型供应商及密钥
python -m uvicorn apps.api.main:app --reload
```

网页：`http://127.0.0.1:8000/`。默认 `MODEL_PROVIDER=fake` 用于测试；真实 Qwen/Ollama Proxy 需按 `.env.example` 配置并重启 API。数据库默认本地 SQLite，可使用 PostgreSQL。部署前必须自行落实生产身份认证、传输安全与备份；演示身份与开发令牌不能用作生产鉴权。

## 当前 API

| Endpoint | 用途 |
|---|---|
| `GET /healthz`、`GET /readyz`、`GET /model` | 健康检查、数据库与模型配置 |
| `POST /conversations`、`GET /conversations`、`GET /conversations/{cid}` | 会话新建、列表、消息历史 |
| `POST /conversations/{cid}/agent/messages` | 唯一正式 Agent 消息写入（含 `content`、`client_request_id` 和可选 `memory_write_consent`） |
| `GET /memories`、`PUT /memories/{key}`、`DELETE /memories/{key}` | 当前用户长期记忆管理 |

原 S1 SSE `POST /conversations/{cid}/messages` 与独立 S2-A `POST /agent/runs` 已在 S2-B2 移除。Agent 返回最终 JSON（非流式），浏览器取消等待并不保证服务端执行停止。

## 测试与验收

```powershell
python -m pytest -q
python -m evals.runner
python -m evals.s2a_benchmark
python -m evals.s2b5_benchmark
```

S2-B5 本地人工运行记录：`37 passed, 3 skipped`；API 集成评测 `11/11 passed`（**脚本模型**）。CI 还包含有限范围 Ruff/mypy、编译与 PostgreSQL Alembic 迁移重复执行检查。需要真实模型验证时，应单独配置 Qwen 和测试任务；请勿把 scripted pass rate 当成模型能力。

## 尚未完成的能力

没有自动记忆提取/语义检索/记忆来源和有效期治理、跨轮原始工具 Observation 恢复、严格并发执行控制、生产级 exactly-once 副作用、官方 Token 预算统计、Agent SSE、服务端主动取消或 Skills/MCP/显式规划。S0/S1 的 Compose 完整运行证据、依赖锁、全量静态检查等也仍有遗留门禁。

## 文档索引

- [09 — 最新开发路线图](docs/09-开发路线图.md)
- [S2 — 通用 Agent 整体实施范围](docs/S2-通用Agent实施范围.md)
- [S0/S1 — 实现与验收](docs/S0-S1-实现与验收.md)
- [S2-A — 实现与验收](docs/S2-A-实现与验收.md)
- [S2-B — 实现与验收](docs/S2-B-实现与验收.md)
- [06 — 总体阶段与验收门禁](docs/06-分阶段实施与验收.md)

S2 专项实施方案与 06 中的部分同名子阶段**不等价**；当前状态以 09 和各阶段已合并实现与验收为准，未关闭的总体质量门禁不会被默认为完成。
