# S2-B — Context、会话状态与受控长期记忆：实现与验收

> 状态：**CLOSED（当前约定的第一版范围）**。基线：PR #4～#8 均经 Standard Merge Commit 合入 `main`，其中 S2-B5 合并提交 `9d00a6cb44d3d414c188dee811d3b1c94910b5d8`。本文件描述 **main 当前事实**，而非每个中间开发切片当时的状态；历史版本可查各 PR 和 Git 历史。

## 1. 总体目标、入口与设计边界

在 S2-A 模型原生 Tool Calling / LangGraph AgentLoop 基线上，把会话历史、运行中工具状态、持久任务、用户长期记忆接到**同一条正式 Agent 主链**：

`POST /conversations/{cid}/agent/messages` → 身份及会话所有权 → 已完成历史 Context Builder → 原 AgentLoop → 白名单 Tools（会话任务与用户记忆）→ 持久化 Turn/Message → 最终 JSON 答案与脱敏 Trace。

不另建专用 Memory Agent / Runtime / 新聊天执行入口。B1 试验期曾同时保留 S1 SSE 和 S2-A 单次 Run HTTP 入口；**B2 已删除** `/conversations/{cid}/messages` 与 `/agent/runs`，不可依照 B1 的旧说明启动。会话创建、列表、读取接口继续存在；Agent 最终回复非 SSE。网页停止仅中断浏览器等待，不保证服务端取消。

S2-B 产物分三种状态：① Conversation / Message / Turn 对话与运行结果，② Conversation-scoped 任务记录，③ owner-scoped 显式键值长期记忆。它们不是同一种记忆，也不保证工具 Observation 的跨轮回放。

## 2. B1～B5 的实际交付

| 切片 | 已合入的实现 | 主要落点 |
|---|---|---|
| B1 — 会话内 Agent | 会话所属用户校验，`client_request_id` 去重，原会话存储用户/助手/Turn，真实模型多轮与 Agent 工具主链接通 | `apps/api/main.py`、`services/db.py` |
| B2 — 单入口与有界 Context | 移除旧 SSE 和 standalone Run 路由/UI 切换；仅采已完成 user/assistant 消息，按完整消息边界和最大消息数/字符预算选最近历史 | `agent/context.py`、`agent/config.py`、`apps/web/index.html` |
| B3 — 会话任务持久化 | `create_task` / `list_tasks` 正式主链通过 ConversationTaskStore 读写 `conversation_tasks`，跨轮、重启保留且按会话隔离 | `services/task_store.py`、`agent/loop.py`、`agent/tools.py`、Alembic 0002 |
| B4 — 受控长期记忆第一版 | `save_memory` / `recall_memory`，按 owner 隔离的 `user_memories`；单请求 `memory_write_consent`，可经已认证 API 查询、更正、删除 | `services/memory_store.py`、`agent/tools.py`、`apps/api/main.py`、Alembic 0003 |
| B5 — 工程评测 | 基于**正式 Conversation HTTP API**的确定性脚本评测，覆盖历史、授权、跨会话/跨用户、纠错、删除、任务、重复请求与重启；并纳入 CI | `evals/s2b5_benchmark.py`、`tests/test_s2b5_evaluation.py`、CI |

### 2.1 正式 HTTP 契约与状态

- `POST /conversations`、`GET /conversations`、`GET /conversations/{cid}`：建立/查询所属会话。
- `POST /conversations/{cid}/agent/messages`：输入 `content`、最少 8 字符的 `client_request_id` 和可选 `memory_write_consent`（默认 `false`）；成功返回最终 JSON，包含 `answer`、`status`、`trace`、`run_id`、`conversation_id`、`message_id` 等。重复 request ID 返回 409；不属于当前用户的会话返回 404；Agent 失败返回 503，且失败助手消息不进入后续 completed 历史。
- `GET /memories`、`PUT /memories/{key}`、`DELETE /memories/{key}`：已认证用户对**自己的**长期记忆进行审查、更正/显式写入和删除；不是另外的 Agent 执行入口。
- 记忆读取依赖模型实际调用 `recall_memory(key)`；不会自动把整张记忆表插入系统提示词。记忆工具结果应作为低信任内容，不能作为高优先级指令。

### 2.2 迁移与上下文范围

数据库 Alembic：`0001_s0_s1` 会话/消息/Turn → `0002_tasks` 会话任务 → `0003_memory` 用户键值记忆。支持新库升级、已版本化数据库升级、已识别旧版无版本表的校验后升级；不承诺任意历史/未知 schema 兼容。

Context Builder 默认 `MAX_HISTORY_MESSAGES=30`、`CONTEXT_MAX_CHARS=24000`，完整选择最近已结束的 user/assistant 消息，保留各角色边界。**字符限制不等于 Token 限制**；超预算时旧事实会遗失，不能宣称摘要或语义检索已完成。

B3 会话任务上限为 20 项；只支持创建/列表，不支持完成态修改/删除。B4 键值记忆 key 最长 80、value 最长 500 字符；用户明确同意的是**本轮保存动作**，不是永久自动学习许可。记忆改写/删除通过带身份的管理接口可操作，模型自主保存必须经过工具层授权。

## 3. 验收证据与关闭结论

| 范围 | 证据与结果 | 证据性质 |
|---|---|---|
| B1/B2 | 真实 Qwen3:14b 网页多轮、计算、上下文引用、隔离、刷新恢复与单入口测试；PR #4/#5 已合并，CI 成功 | 有限真实模型人工验收 + 自动化 |
| B3 | 真实 Qwen 创建/跨轮读取/重复创建、后端重启保留、会话隔离，用户确认符合预期；PR #6 合并 `5295d8f8cbec`，main CI SUCCESS（Actions `39227742866`） | 人工 + 自动化 |
| B4 | 用户真实 Qwen 测试：授权保存、跨会话查回、无授权改写被拒、再次读回原值；PR #7 合并 `51bf699b142a`，main CI SUCCESS（Actions `39231276219`） | 人工 + 自动化 |
| B5 | 用户 Windows `python -m pytest -q`：**37 passed, 3 skipped**；`python -m evals.s2b5_benchmark`：**11/11 passed**，报告 `evals/results/s2b5/offline-v1.json`；PR #8 合并 `9d00a6cb44d3`，main CI SUCCESS（Actions `39350013620`） | **脚本化**正式 API 集成验证，非真实模型准确率 |

B5 11 场景涵盖：history context budget / retention、拒绝未授权记忆写入、同意写入、跨会话读取、owner 隔离、拒绝覆盖且保留原值、更正可见、删除不可读、任务会话隔离、重复请求 ID、服务重启状态。

`3 skipped` 不等于 3 passed。CI 除 pytest 外还覆盖 `evals.runner`、`evals.s2a_benchmark`、`evals.s2b5_benchmark`、编译、**限定范围** ruff/mypy、PostgreSQL Alembic 升级和幂等复跑。用户本地报告不纳入仓库（`evals/results/` 被忽略），需按命令复现。

### 3.1 复现命令

```powershell
cd D:\HealthAgent
.\.venv\Scripts\Activate.ps1
git switch main
git pull --ff-only
python -m pytest -q
python -m evals.s2b5_benchmark
python -m uvicorn apps.api.main:app --reload
```

真实模型需预先配置 `MODEL_PROVIDER=ollama_proxy` 及相关参数；通过 `http://127.0.0.1:8000/` 在**原单一 Agent UI**上进行人工测试，不能以脚本化 pass rate 冒充 Qwen 正确率。

## 4. 已知限制与后续工作

- **B5 11/11 是 deterministic scripted fixture**；尚未提供 Qwen 的大样本跨轮长期记忆召回率、工具选择准确率、置信区间、重复实验及长对话失败率。
- B4 只做**精确键**读写，不是自动记忆提取、向量语义召回、来源置信度、冲突决策、TTL 或审批队列；S2 通用实施范围中的来源/有效期/比较检索仍是未来目标，不能因 B4 CLOSED 当作达成。
- B2 只做字符预算与完整消息选择，非官方 Token 计数、自动摘要或关键事实保证。
- 原始工具 Observation/Trace 未做跨 Run 持久化与恢复；前端仅显示本轮 Trace；任务工具写库发生在最终模型回复前，失败后仍可能存在真实副作用，不保证 exactly-once。
- 同会话并发严格串行、任务 20 条配额原子性、服务端主动取消、实时 Agent SSE、生产认证和广覆盖 lint/mypy 等未关闭；S0/S1 遗留门禁不会随着 B 阶段关闭自动关闭。
- S2-C Skills/MCP/Harness 和 S2-D 显式 Planning/Replanning 是后续增量；仍坚持唯一正式会话 Agent 入口。

## 5. 追溯与文档分工

- 总体专项规划：[S2-通用Agent实施范围](S2-通用Agent实施范围.md)；状态入口：[09-开发路线图](09-开发路线图.md)；早期基础：[S0-S1-实现与验收](S0-S1-实现与验收.md)；循环：[S2-A-实现与验收](S2-A-实现与验收.md)。
- 实现演进：PR [#4](https://github.com/cxjchelsea/HealthAgent/pull/4) → [#5](https://github.com/cxjchelsea/HealthAgent/pull/5) → [#6](https://github.com/cxjchelsea/HealthAgent/pull/6) → [#7](https://github.com/cxjchelsea/HealthAgent/pull/7) → [#8](https://github.com/cxjchelsea/HealthAgent/pull/8)。
- B1～B5 原分阶段文档在收口后从当前文档目录移除，历史仍在 Git/PR 可追溯。单独的 `S2-B-通用实施范围.md` 实际**不存在**；`S2-通用Agent实施范围.md` 包含 S2-A/C/D，必须保留。
