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

## 首轮真实 Qwen 结果与集中诊断（2026-11-09）

收到真实运行结果：`qwen3:14b` / `ollama_proxy` / Unified / 2 repeats，**18/22 PASS**，中位响应 **6009 ms**。唯一重复失败的是 `list_tasks`（2/2：HTTP 200 completed，无 tool event、无 Attempt）和 `mcp_reference_lookup`（2/2：`needs_input`，无 tool event、无 Attempt）。其他普通读、记忆、无工具、Shell 拒绝、计划创建/首步/恢复均通过；这些结果仅证明各自测试的观测事实，并不自动证明全面语义质量。

**当前证据不支持断言 Registry Adapter 出错**：两个失败路径均没有派发；尚未获得 Primary route/Host reason，必须先区分模型选择 `respond/clarify` 与 Host deny 的责任边界。切勿为使分数变绿添加中文关键词硬编码，或放松 Host。

已改进 `evals/s25_05_step5_live.py`：输出 `primary_route`、`host_disposition`、`host_reason` 和稳定 `refusal_code`（不存储用户原始输入、模型回答、工具参数、明文记忆或敏感日志）；对原有两个用例增加语义相同的显式工具名对照用例 `list_tasks_explicit`、`mcp_explicit_term`，并有离线评分/脱敏测试。

### 集中复验

在相同本地 `ollama_proxy/qwen3:14b` 环境、PR #33 新 HEAD 下：

```powershell
cd D:\HealthAgent
git status --short
git fetch origin
git switch feat/s25-05-step4-tool-registry-slice-20261106
git pull --ff-only
.\.venv\Scripts\Activate.ps1
$env:MODEL_PROVIDER = "ollama_proxy"
$env:MODEL_NAME = "qwen3:14b"
$env:RUN_S25_05_LIVE = "1"
python -m evals.s25_05_step5_live --repeats 2 --cases list_tasks,list_tasks_explicit,mcp_reference_lookup,mcp_explicit_term --output artifacts\s25-05-step5-routing-diagnostic.json
```

判定规则：若原提示失败、显式提示成功且 Primary route 不同，优先集中整改主语义 Tool-or-Respond/Clarify 提示与对照评测；若 Primary ordinary/read_only/Host allow 但无调用，检查 AgentLoop 工具曝光、决策及工具调用；若 Host restrict/deny，检查准入事实。只有确认实际根因后才修改生产路由。**不能将只含 4 组的局部复验视为完整 Step 5 通过**。

保持 Draft，不合并，不默认开启。

## Windows 临时 SQLite 清理故障整改（2026-11-09）

本地定向测试复现 `PermissionError [WinError 32]`，Windows 拒绝删除 `%TEMP%/s25-05-live-*/isolated-0.sqlite`。定位到验收工具自身创建的第二个 `database(settings.database_url)` 的独立 SQLAlchemy Engine 没有显式 `dispose()`；API 的 TestClient 生命周期仅关闭 API 持有的 Engine，不会自动释放这个证据读取 Engine。修复为在 `isolated_read_sessions` context manager 的 `finally` 中释放该池，保证其在 `TemporaryDirectory` 退出前释放。增加成功、异常和 SQLite 删除的离线测试。**此故障不属于模型/Host/Registry 失败，也没有产生有效的新 8-case 诊断报告。**

更新到修复提交后运行同样的 4 个用例、2 次重复即可，不必清空或覆盖原始 `artifacts/`。如果再次出现锁文件异常，先检查是否有外部进程在使用临时数据库；不得直接删除还在使用中的 SQLite 文件。针对已经遗留且确认无进程使用的临时测试数据库，可在验收结束后人工清理；脚本不会自动清理其他旧临时目录。

## Step 5 定向诊断与有限整改（2026-11-09）

真实 `ollama_proxy/qwen3:14b` 定向对照：**2/8 PASS**。

- `list_tasks` 两轮：Primary `ordinary_tool`，Host `allow/read_only`，但无工具 Trace/Attempt；显式 `list_tasks_explicit` 两轮均调用 `list_tasks`，Attempt version `1.0.0`。因此权限和 Registry Adapter 正常，失败在模型首次工具决策。
- `mcp_reference_lookup` 两轮：Primary `clarify` / Host `primary_v1_clarification_required`，但自然语言已包含查询对象 RAG，属于主语义误澄清。 `mcp_explicit_term` 两轮：`primary_route_invalid_signal`；原诊断 **未捕获安全 schema issue 字段**，不能断言具体格式错误。
- 同一批中 Host 和 Adapter 均未出现越权执行，不能据此推断 MCP 后端错误。

已进行针对性整改：
1. HTTP Unified 主路由准入 ordinary/read_only 时，`AgentLoop` 若无任何工具 Observation，**仅重决策一次**，仍不调用则返回 `ordinary_read_unverified`、不把未核实答案记为完成；不硬编码选择 `list_tasks` 或任何关键词，不改变 Registry/Host 授权，也不影响直接 Legacy 调用。增加单元回归。
2. Primary 系统提示增强“新检索已提供具体术语”与“确实缺少术语”的对比；不绑定 RAG 字符串，不允许绕过原 Schema/Host 校验。
3. 验收 JSON 增加已由 `safe_route_validation_issues` 白名单化的 `validation_issues` 字段，用于定位显式 MCP 失败，不存放模型原始响应。
4. 后续 **只重复失败的 4 类场景**，不将自动化 CI 通过冒称真实模型重新通过。

*验收裁决*: 代码修复需精确 HEAD CI，真实 Qwen 故障仍需新报告复验。保留 PR #33 Draft，未授权合并。
