# HealthAgent — S0/S1 可运行基线

当前范围：**通用对话基础**，不是具备自主决策和医疗能力的完整 Agent。此源码与 V4.2 文档的 S0–S1 对应。

## 运行（Windows PowerShell / WSL / macOS / Linux）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env  # Windows 可改用 Copy-Item .env.example .env
uvicorn apps.api.main:app --reload
```

访问 http://127.0.0.1:8000 。默认 `fake` 供应商只供测试，不是 LLM。

真实模型：将 `.env` 的 `MODEL_PROVIDER=openai_compatible`，并填写 `MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`，重启 API。兼容 OpenAI 的 Chat Completions 流式接口；部分服务可能有不兼容差异。**本环境未完成真实模型 E2E。**

Docker：`docker compose up --build`（自带 PostgreSQL；不对公网提供身份认证）。

## API

- `GET /healthz` 进程存活
- `GET /readyz` 数据库就绪、模型配置状态（未配置模型时仍可返回 ready，检查字段）
- `GET /model` 当前模型配置（不暴露秘钥）
- `POST /conversations` 创建会话
- `GET /conversations` 查询自己的会话
- `GET /conversations/{id}` 获取历史
- `POST /conversations/{id}/messages` JSON `{ "content":"你好", "client_request_id":"任意唯一至少8字符字符串" }`，返回 SSE `started`、`message_delta`、`usage`（字符估算）、`message_done` 或 `error`。

## 测试

```bash
pytest -q
python -m evals.runner
```

评测生成在 `evals/results/smoke/`：5 条**确定性字符串测试**，不代表模型能力评测。

## 安全/限制

- 默认 `ALLOW_DEMO_IDENTITY=true` 仅用于本机演示。服务如果部署给他人，必须增加真正的认证、传输安全和身份隔离；`USER_TOKENS` 也是开发模拟鉴权，不是生产方案。
- 目前前端是无构建的 HTML/JavaScript，不是最终 React 工作台。
- SSE 断线取消依赖服务器断线检测；不同代理部署下的取消一致性仍需真实测试。部分断线情况下写入会延迟，异常响应不会成为完整助手历史。
- 数据表为 S0 的 `create_all`，未引入正式迁移框架；生产前需要 Alembic 与备份策略。
- 尚未实现 S1 的独立结构化语义解析器、真实五轮连续对话 E2E、模型用量官方 Token 统计、后端明确取消 API 与 10 条真实模型运行证据；因此 S1 **未完全验收**。
- S2 Planning、Tool Calling、Memory、Harness、MCP，以及健康业务均未实现。

## 来源

设计基线：V4.2 `06-分阶段实施与验收.md`，S0–S1。代码中刻意不纳入医疗领域逻辑。
