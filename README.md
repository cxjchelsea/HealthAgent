# HealthAgent — S0/S1 可运行基线

当前范围：**通用对话基础**，不是具备自主决策和医疗能力的完整 Agent。此源码与 V4.2 文档的 S0–S1 对应。

## 项目目录

下面只列纳入版本库的源码与设计文档。本地运行会再生成 `.venv/`、`.env`、`healthagent.db`、`__pycache__/`、`.pytest_cache/`、`*.egg-info/`、`evals/results/`，这些已在 `.gitignore` 中忽略，不需要手改。

```text
HealthAgent/
├── agent/                 模型配置与供应商适配
│   ├── __init__.py        包标记，使 agent 可被导入
│   ├── config.py          从环境变量和 .env 读取运行配置
│   └── providers.py       fake / OpenAI 兼容 / Ollama 代理三种流式供应商
├── apps/
│   ├── __init__.py        包标记
│   ├── api/
│   │   ├── __init__.py    包标记
│   │   └── main.py        FastAPI 应用：健康检查、会话、SSE 对话、静态页
│   └── web/
│       └── index.html     无构建的单页聊天演示（由 GET / 直接返回）
├── services/
│   ├── __init__.py        包标记
│   ├── db.py              SQLAlchemy 模型：会话、消息、轮次，以及引擎工厂
├── evals/
│   ├── __init__.py        包标记
│   ├── runner.py          确定性字符串评测入口（python -m evals.runner）
│   └── datasets/
│       └── smoke-v1.jsonl 5 条固定样例，只验证归一化，不调用模型
├── tests/                 pytest 套件（见 pyproject.toml 的 testpaths）
│   ├── test_api.py        健康检查、鉴权隔离、SSE 持久化、重复请求、失败不落库、多轮上下文、页面
│   ├── test_closure.py    中途超时不算完成、完整流的用量与结束事件
│   ├── test_eval.py       断言 smoke 评测 5 条全部通过
│   ├── test_ollama_proxy.py  用假 HTTP 校验 Ollama 代理的流解析、错误不外泄、输出上限
│   └── test_live_optional.py 可选真实模型五轮对话，默认跳过
├── docs/                  V4.2 设计文档，描述目标形态，不是当前已实现代码
│   ├── 01-项目定位与最终形态.md
│   ├── 02-功能需求与场景设计.md
│   ├── 03-系统总体架构.md
│   ├── 04-Agent核心技术设计.md
│   ├── 05-领域能力与数据设计.md
│   ├── 06-分阶段实施与验收.md   阶段划分与验收门槛
│   ├── 07-Agent-Harness研究与实验设计.md
│   ├── 08-八大智能能力模型与评测.md
│   └── 09-开发路线图.md         已实现的 S0/S1 能力与后续阶段
├── .github/workflows/ci.yml  推送与 PR 时跑 pytest、评测和 compileall
├── .gitignore             忽略密钥、本地库、虚拟环境和评测产物
├── compose.yml            本机 PostgreSQL 16 + API 容器编排
├── Dockerfile             Python 3.12 镜像，安装本包并启动 uvicorn
├── pyproject.toml         包名、依赖、pytest/ruff 配置
├── README.md              运行方式、接口、限制与本目录说明
└── STATUS.md              S0/S1 已测项与未完成门槛
```

各目录职责：

- `agent/`：不包含规划、工具或医疗逻辑。`config.py` 的 `Settings` 决定数据库、模型供应商、超时、历史条数和演示身份。`providers.py` 把上游流统一成异步文本片段；`fake` 只回显固定句子，`openai_compatible` 走 Chat Completions，`ollama_proxy` 走 Ollama 风格的 `/chat` NDJSON，并丢掉 `<think>` 推理文本。
- `apps/api/main.py`：创建表、校验身份、管理会话与消息，把模型输出编成 SSE（`started`、`message_delta`、`usage`、`message_done`、`error`）。断线或供应商失败不会把半成品记成已完成历史。
- `apps/web/index.html`：浏览器里的发送、停止、新建和恢复会话。停止只取消当前 fetch，没有单独的后端取消接口。
- `services/db.py`：`Conversation`、`Message`、`Turn` 三张表。本地默认 SQLite；Compose 里改用 PostgreSQL。表结构仍由启动时 `create_all` 建立。
- `evals/`：对照 `expected_normalized` 做去空白、小写比较，写出 `metrics.json` 和 `manifest.json`。结果目录是运行产物。
- `tests/`：默认不访问外网。`test_live_optional.py` 只有设置 `RUN_LIVE_MODEL_TEST=1` 且配置了真实供应商时才执行。
- `docs/`：01–08 是产品与架构设计，`09-开发路线图.md` 记录当前代码已经实现的 S0/S1 能力以及 S2 之后的顺序。文档开头提到的 `traceability.json` 尚未入库。

不入库、但本机目录里常见的文件：

| 路径 | 作用 |
|---|---|
| `.env` | 本地配置。当前示例键包括 `DATABASE_URL`、`MODEL_PROVIDER`、`MODEL_BASE_URL`、`MODEL_NAME`、超时与 `ALLOW_DEMO_IDENTITY`。不要提交。 |
| `healthagent.db` | 本地 SQLite 数据库，由 `.env` 的 `DATABASE_URL=sqlite:///./healthagent.db` 指定。 |
| `.venv/` | 本地虚拟环境。 |
| `healthagent_s0_s1.egg-info/` | `pip install -e .` 生成的安装元数据，目录名来自包名 `healthagent-s0-s1`。 |

`healthagent.db` 由 `services/db.py` 在启动时建表，里面有三张表：`conversations`（会话）、`messages`（消息）、`turns`（每一轮请求的状态和耗时）。页面上创建的对话和聊天记录都写在这里。删掉这个文件后，下次启动 API 会自动建一个空库，已有会话会消失。`.gitignore` 已忽略 `*.db`。

`healthagent_s0_s1.egg-info/` 是 setuptools 写下的包说明、依赖列表和源码文件清单。它告诉当前虚拟环境：这个项目是以可编辑方式安装的，改 `agent/`、`apps/` 等代码后不用重新安装。删掉后需要再执行一次 `pip install -e '.[dev]'` 才会重新生成。该目录同样被 `.gitignore` 忽略。

## 运行（Windows PowerShell / WSL / macOS / Linux）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e '.[dev]'
# 本地配置写在已忽略的 .env 中（仓库没有 .env.example）
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
