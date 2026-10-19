# HealthAgent — S0/S1 对话基线与 S2-A 工具循环

当前代码包含两部分：S0/S1 的通用对话，以及 S2-A 的有界工具循环（模型原生工具调用、LangGraph 决策、四个白名单工具）。记忆、MCP、显式规划和健康业务还在后续阶段。

## 项目目录

下面只列纳入版本库的源码与设计文档。本地运行会再生成 `.venv/`、`.env`、`healthagent.db`、`__pycache__/`、`.pytest_cache/`、`*.egg-info/`、`evals/results/`，这些已在 `.gitignore` 中忽略。

```text
HealthAgent/
├── agent/                      配置、对话供应商、S1 目标收录、S2-A 循环
│   ├── __init__.py             包标记，使 agent 可被导入
│   ├── config.py               从环境变量和 .env 读取运行配置
│   ├── providers.py            fake / OpenAI 兼容 / Ollama 代理的流式对话
│   ├── understanding.py        规则型目标收录：信息请求、普通对话、缺指代时要求澄清
│   ├── decision.py             向供应商请求原生 tool_calls，不把正文里的 JSON 当成工具调用
│   ├── loop.py                 LangGraph 循环：决策 → 工具观察 → 再决策，限制步数
│   ├── tools.py                白名单工具：calculate、utc_time、create_task、list_tasks
│   └── prompts/
│       └── s2a_core_v1.md      S2-A 系统策略，版本号 s2a-core-v1
├── apps/
│   ├── __init__.py             包标记
│   ├── api/
│   │   ├── __init__.py         包标记
│   │   └── main.py             FastAPI：健康检查、会话 SSE、/understanding、/agent/runs、静态页
│   └── web/
│       └── index.html          单页：普通聊天，以及 S2-A Agent 测试模式
├── services/
│   ├── __init__.py             包标记
│   ├── db.py                   SQLAlchemy 模型：会话、消息、轮次，以及引擎工厂
│   └── migrate.py              启动迁移入口：新库升级，已有未版本化库核对后盖章
├── alembic/
│   ├── env.py                  迁移使用与 API 相同的模型和 DATABASE_URL
│   └── versions/
│       └── 0001_initial_s0_s1.py  初始三张表，修订号 0001_s0_s1
├── evals/
│   ├── __init__.py             包标记
│   ├── runner.py               S0 确定性字符串评测（python -m evals.runner）
│   ├── s2a_benchmark.py        S2-A 的 20 条任务判分；--real 才调用真实模型
│   └── datasets/
│       ├── smoke-v1.jsonl      5 条归一化样例，不调用模型
│       └── s2a-20-v1.jsonl     20 条工具任务：计算、时间、模拟任务、失败与越权负例
├── tests/
│   ├── test_api.py             健康检查、鉴权隔离、SSE 持久化、重复请求、失败不落库、多轮上下文、页面
│   ├── test_closure.py         中途超时不算完成、完整流的用量与结束事件
│   ├── test_eval.py            断言 smoke 评测 5 条全部通过
│   ├── test_ollama_proxy.py    用假 HTTP 校验 Ollama 流解析、错误不外泄、输出上限
│   ├── test_live_optional.py   可选真实模型五轮对话，默认跳过
│   ├── test_migration.py       空库迁移，以及已有数据盖章后不丢失
│   ├── test_understanding.py   缺指代必须澄清，明确的信息请求不误判
│   ├── test_s2a_loop.py        沙箱计算、多步工具、步数上限、非法工具、可注入时钟
│   ├── test_s2a_recovery.py    工具失败后依据观察修正，而不是谎称成功
│   ├── test_s2a_api.py         /agent/runs 的身份校验与脱敏 Trace
│   ├── test_s2a_web.py         页面同时提供普通聊天和 Agent 模式
│   ├── test_s2a_benchmark.py   离线契约、错误标题、同批调用不能冒充观察后重规划
│   ├── test_s2a_dynamic_dependency.py  先读 UTC 再写入该时间的任务标题
│   └── test_s2a_live_optional.py  可选真实模型原生工具调用，默认跳过
├── docs/
│   ├── 01-项目定位与最终形态.md
│   ├── 02-功能需求与场景设计.md
│   ├── 03-系统总体架构.md
│   ├── 04-Agent核心技术设计.md
│   ├── 05-领域能力与数据设计.md
│   ├── 06-分阶段实施与验收.md          阶段划分与验收门槛
│   ├── 07-Agent-Harness研究与实验设计.md
│   ├── 08-八大智能能力模型与评测.md
│   ├── 09-开发路线图.md                当前进度：S2-A 已关闭，下一阶段是 S2-B
│   ├── S0-S1-实现与验收.md             S0/S1 已交付能力、证据和仍未关闭的门禁
│   ├── S2-通用Agent实施范围.md         现行 S2-A 到 S2-D 四段，与 06 的同名子阶段不对等
│   └── S2-A-实现与验收.md              S2-A 已关闭：实现、证据、判分演进和人工验收
├── .github/workflows/ci.yml    pytest、两套评测、编译、部分 ruff/mypy、PostgreSQL 迁移
├── .gitignore                  忽略密钥、本地库、虚拟环境和评测产物
├── .env.example                可提交的配置模板，复制为 .env 后填写本机值
├── alembic.ini                 Alembic 日志与脚本位置；连接串以 DATABASE_URL 为准
├── compose.yml                 本机 PostgreSQL 16 + API 容器编排
├── Dockerfile                  Python 3.12 镜像，安装本包并启动 uvicorn
├── pyproject.toml              包名、依赖（含 Alembic 与 LangGraph）、pytest/ruff 配置
├── README.md                   运行方式、接口、限制与本目录说明
└── STATUS.md                   早期 S0/S1 记录，仍写着 S2 未开始；现行状态以 09 和两份实现与验收文档为准
```

各目录职责：

- `agent/`：`config.py` 与 `providers.py` 继续负责对话配置和流式供应商。`understanding.py` 用少量规则标记目标种类，缺指代时要求澄清，不代替模型推理。S2-A 由 `decision.py` 取原生工具调用，`loop.py` 用 LangGraph 在决策和工具之间循环，默认最多 6 步、每步最多 4 个工具。`tools.py` 只允许四则运算、UTC 时间，以及本次运行内的模拟任务；不访问网络，也不执行宿主机命令。
- `apps/api/main.py`：启动时调用 `services.migrate`。对话接口仍输出 SSE。新增 `POST /understanding` 和 `POST /agent/runs`。Agent 接口返回最终 JSON（答案、状态、脱敏 Trace），不流式输出，也不把工具参数和原始观察返回给页面。
- `apps/web/index.html`：可在普通聊天和 “Agent S2-A” 之间切换。Agent 模式是一次 Run，不保留多轮任务。页面上的停止只中断浏览器等待。
- `services/` 与 `alembic/`：表仍是 `conversations`、`messages`、`turns`。`migrate.py` 对空库执行升级；对已有且结构一致的旧库只写入版本号，不删数据。结构不一致时拒绝自动迁移。
- `evals/`：`runner.py` 仍是 5 条字符串归一化。`s2a_benchmark.py` 默认用脚本决策检查判分器；设置 `RUN_S2A_LIVE=1` 后加 `--real` 才请求真实模型。判分检查表达式、数值、任务标题和终态。报告只保留每项检查的真假。
- `tests/`：默认不访问外网。带 `live` 标记的用例需要 `RUN_LIVE_MODEL_TEST=1` 或 `RUN_S2A_LIVE=1`。
- `docs/`：01–08 是产品与架构设计。`09-开发路线图.md` 只记阶段状态和下一步，当前基线是 S2-A 已关闭、下一步 S2-B。`S0-S1-实现与验收.md` 和 `S2-A-实现与验收.md` 分别记录已交付能力、复现命令和未关闭项；原先分开的验收证据、第二轮判分和人工验收已经并入这两份。`S2-通用Agent实施范围.md` 的 S2-B/C/D 与 `06` 里的同名子阶段不是同一套划分。文档开头提到的 `traceability.json` 尚未入库。

不入库、但本机目录里常见的文件：

| 路径 | 作用 |
|---|---|
| `.env` | 本地配置，由 `.env.example` 复制而来。不要提交。 |
| `healthagent.db` | 本地 SQLite。启动时由 Alembic 建表或盖章，保存会话、消息和轮次。 |
| `evals/results/s2a/real.json` | 第一轮真实模型报告，只比较工具序列。按 `S2-A-实现与验收.md` 第 6 节，不能把它当成带参数和终态的通过证据。 |
| `evals/results/s2a/real-v2.json` | 证据判分报告。首次严格结果曾是 17/20，判分修正后的复测为 20/20（`s2a-evidence-v2`）。重新运行 `--real` 会覆盖它。 |
| `.venv/` | 本地虚拟环境。 |
| `healthagent_s0_s1.egg-info/` | `pip install -e .` 生成的安装元数据，目录名来自包名 `healthagent-s0-s1`。 |

`healthagent.db` 里的三张表由 `services/db.py` 定义。删掉这个文件后，下次启动会迁出一个空库，已有会话会消失。`.gitignore` 已忽略 `*.db`。

`healthagent_s0_s1.egg-info/` 告诉当前虚拟环境：这个项目是以可编辑方式安装的，改 `agent/`、`apps/` 等代码后不用重新安装。删掉后需要再执行一次 `pip install -e '.[dev]'`。该目录同样被 `.gitignore` 忽略。

## 运行（Windows PowerShell / WSL / macOS / Linux）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e '.[dev]'
# 复制 .env.example 为 .env 后按本机修改；不要提交 .env
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
- 启动时走 Alembic（`services/migrate.py`，修订号 `0001_s0_s1`）。已有且结构一致的旧库只盖章，不删数据；生产前仍需要备份策略。
- `agent/understanding.py` 只做有限的规则型目标收录。真实五轮证据、官方 Token 和后端主动取消仍以 `docs/S0-S1-实现与验收.md` 为准。
- S2-A 已有原生工具调用和有界循环。记忆、MCP、显式规划以及健康业务尚未实现。

## 来源

设计基线：V4.2 `06-分阶段实施与验收.md`，S0–S1。代码中刻意不纳入医疗领域逻辑。
