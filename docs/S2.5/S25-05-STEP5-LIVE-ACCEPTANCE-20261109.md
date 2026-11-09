# S25-05 Step 5 — 集中真实模型验收（2026-11-09）

## 验收边界

Step 4 已通过代码/CI 技术门禁；Step 5 在真实模型环境中验证模型**是否真正选对工具**、Host 是否准入、Observation 与持久化证据是否一致。**GitHub CI 中的 fake/scripted 模型不能充当真实 Qwen 测试。**

本阶段优先用一个自动化工作包集中得到所有反馈，失败再合并整改，不为单个案例拆出额外开发轮次。

## 本机一键运行（Windows / PowerShell）

在 `D:\HealthAgent` 根目录，先将 PR #33 最新代码取到本地。以下命令假定已完成 Git 安全检查、工作区改动已保存，且正在 PR #33 分支（不要在有未提交改动时执行强制覆盖）：

```powershell
cd D:\HealthAgent
git status --short
git fetch origin
git switch feat/s25-05-step4-tool-registry-slice-20261106
git pull --ff-only
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# 与已有的 Ollama Proxy 配置一致；此处仅作示例，按本机实际值设置
$env:MODEL_PROVIDER = "ollama_proxy"
$env:MODEL_NAME = "qwen3:14b"
# MODEL_BASE_URL 在 .env 或环境变量中配置为已验证可访问的代理地址
# 如已有 .env，通常不需要在终端重复输入密钥

$env:RUN_S25_05_LIVE = "1"
python -m evals.s25_05_step5_live --repeats 2 --output artifacts\s25-05-step5-live.json
```

脚本不会连接当前 `DATABASE_URL` 中的业务数据库。每个重复周期使用独立临时 SQLite，创建真实 API、实际模型决策和真实 Tool/Plan 执行；完成后删除 SQLite，仅保存汇总证据。例子不会启动或删除用户正在运行的 API 服务。

**若本机模型地址、凭据、工作区状态不符合预期，先停止并检查配置，不要通过改为 fake 来强行取得 PASS。**

### 可分段定位

第一次可以只测三项普通只读工具：

```powershell
python -m evals.s25_05_step5_live --repeats 1 --cases text_stats,calculate,mcp_reference_lookup --output artifacts\s25-05-step5-read.json
```

一次性完整套件默认覆盖：`text_stats`、`calculate`、`utc_time`、`list_tasks`、`mcp_reference_lookup`、`recall_memory`、`no_tool`、`deny_shell` 及同一会话中的 `plan_create → plan_first → plan_resume`。所有普通只读 Registry 旗标仅在临时的 `Settings` 对象中启用；不修改持久的本机 `.env`，更不会修改生产开关。

### 必须查看的物理证据

脚本输出 JSON：
- `pass`、HTTP、终态和稳定 `termination_reason`；
- Trace 中真实执行的工具名、成功工具名；
- `AgentAttempt` 与 `PlanToolDispatch` 数量和工具版本；
- 模型 HTTP 调用次数、请求耗时；
- `plan_create` 的未执行、`plan_first` 的一条 Checkpointed Dispatch、`plan_resume` 的两条 Checkpointed Dispatch。

不会写出用户 prompt、回答正文、密钥、内存事实内容、数据库 URL、工具原始参数及对话记录。普通调用要求 **真实成功 Trace + 成功 Attempt + Registry 版本 `1.0.0`**，不能只凭模型回复声称成功。保存计划需要原始数据库 Checkpoint 与对应的 `legacy-plan-v1` Dispatch。

`all_passed` 为 true 才算该组真实场景自动化通过。脚本进程退出码 1 表示有失败，具体案例按 `cases` 定位；未运行、错误、跳过依赖场景均**不得记录为 PASS**。对真实模型的自然语言答案质量仍需人工复核。

## 已覆盖与未覆盖

- CI/SQLite：真实 API 和数据库的结构、参数/权限、持久化和版本门禁；脚本评分单元测试；
- CI/PostgreSQL：M4 并发 Claim、子进程崩溃、离线恢复、双 Worker、Alembic；
- 用户本机真实模型：模型路由选择、Tool Calling 行为与回答质量 —— **必须运行本脚本取得新证据**；
- 本脚本不覆盖 `save_memory` / `create_task` 等写操作，也不以本地 SQLite 替代 PostgreSQL 崩溃物理证明；
- 如果某个自然语言 case 被模型拒绝或偏航，应记录为 FAIL，然后按同一批数据集中分析原因，不应逐案打补丁或用关键词硬编码通过。

## 进入 Step 6 的门禁

1. 精确 HEAD CI 完全通过；
2. 本机真实 `ollama_proxy/qwen3:14b` 套件保存完整结果，按工具调用证据判定；失败必须归因并复测；
3. 人工检查非工具回答和 MCP 返回事实的忠实性、计划建立/首步/恢复语义；
4. 独立审查 Host 不越权、M4 不无证重放、版本升级不自动重标、Pending/写入权限无扩展；
5. PR #33 合并需用户单独授权，仍保持 Draft。
