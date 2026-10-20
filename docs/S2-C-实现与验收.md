# S2-C — Skills / MCP / Harness：实现与验收

## 当前交付：可运行的第一条纵向链路（待真实模型验收）

本阶段在既有正式 `POST /conversations/{cid}/agent/messages` → Context Builder → AgentLoop → Tool Executor 主链上增加 **可信静态 Skills manifest**、任务级工具可见集合，以及受控模拟 MCP Catalog 工具。没有新建 Agent 入口或平行调度器。

- `agent/skills.py`：五个可信 manifest（arithmetic、clock、tasks、memory、reference）；`dynamic` 按任务词线索暴露工具、无法判断时回退全工具；`all` 用于对照。规则只做**工具发现/预筛选**，真实行动仍由模型 Tool Calling 决定。
- `agent/loop.py`：将本轮允许工具 Schema 给模型，并在执行器再次检查 `tool_not_exposed`，模型凭空请求未暴露工具不能执行。按 run 冻结选择，不会在后续回合自动重新路由。
- `agent/mock_mcp.py`：封闭的 in-process 模拟 MCP 服务适配器，只有 `mcp_reference_lookup(term)`，可信静态术语目录、显式白名单及 Pydantic 严格参数检验；不是网络 MCP Client，也没有真实 JSON-RPC/stdio/HTTP MCP 握手、远端权限、隔离或超时保障。
- `agent/tools.py`：白名单新增模拟 `mcp_reference_lookup`，正式 Host 注入 MockMCPClient。未经允许或工具不在候选集合中不可执行。
- `tests/test_s2c_skills.py`：验证 dynamic/all 工具选择、模糊回退、非法工具与额外参数、正式 API 查询，以及模型请求未暴露工具时执行层拒绝。

## 局限与后续 S2-C 开发

首版并未达到整个 S2-C CLOSED：需要真正符合 MCP 协议的隔离模拟 Server/Client、能力协商与错误边界；可信 Skills 的清单与完整内容分离加载、版本与 token/char 预算；带明确统计的全工具暴露 vs 动态选工具基准（正确率、延迟、提示开销），并在真实 Qwen3:14b 上人工检验，控制误过滤。

特别注意：当前关键词路由容易错过隐含工具意图，因此遇到不确定表达必须回退全工具。不可把这种简单策略描述成模型驱动智能规划或可靠安全授权。文档仍保留 S2-C 为 IN PROGRESS，直到完整约定范围验收。

## 本地复现

```powershell
git fetch origin
git switch feat/s2c-skills-mcp-harness
git pull --ff-only
python -m pytest -q
python -m uvicorn apps.api.main:app --reload
```

网页在已有会话里问“请用参考资料工具解释 MCP 术语”，观察 `mcp_reference_lookup` 的成功/失败 Trace，再问计算与任务、记忆，核实既有链路无回归。真正 Qwen 工具选择应另行计入人工验收，而不是借测试脚本声称成功。

## 增量集成：隔离 stdio MCP / Skills 分级加载 / 对照 Harness

新增 `agent/mcp_stdio_server.py` 作为独立 Python 子进程内运行的受信任参考 Server；`agent/mcp_stdio_client.py` 通过 stdio JSON-RPC 2.0 的 `initialize`、`notifications/initialized`、`tools/list`、`tools/call` 完成协议消息交互，验证协议版本和工具清单，仅允许调用固定工具；超时、未知工具、格式错误均返回受控错误。正式会话 API 现注入 `StdioMCPClient`，原进程内 MockMCPClient 仅用于受信任服务的工具实现。

**协议范围说明：** 本版为固定的单服务、单工具 stdio MCP JSON-RPC 协议纵向切片；暂未用第三方 MCP SDK 互操作测试、未实现资源、提示词、取消、进度通知、动态服务器注册或网络传输。子进程隔离不等同于 OS 安全沙箱，不允许模型决定启动命令；只通过固定 `python -m agent.mcp_stdio_server` 调用受信任服务。

`agent/skills.py` 将精简 manifest 与正文 instructions 分离，只有选择后的正文才加入系统上下文。增加 `SKILL_STRATEGY=dynamic|all`、`SKILL_BUDGET_CHARS`（默认 600）配置；超过预算拒绝执行而非静默截断。当前正文仍是受信任的代码内静态文本，没有从未审计文件目录动态加载或第三方 Skills 发现。

`evals/s2c_benchmark.py` 对十条固定目标运行 `all` 与 `dynamic`，比较预期工具覆盖率、暴露工具数量、Schema 字符开销、已加载 Skill 文本长度及选择器执行时间；`python -m evals.s2c_benchmark` 输出 `evals/results/s2c/selection-v1.json`。**这不是 LLM 准确率、token 价格或真实端到端延迟比较。** `tests/test_s2c_integration.py` 包含 stdio 握手+查询、非法输入、预算边界和对照指标断言。

最终 S2-C CLOSED 前，还需要真实 Qwen 多场景 dynamic/all 对照、真实 MCP SDK 互操作性及安全异常测评。本阶段文档持续维护，不增加第二份子阶段验收文件。

## S2-C 增补：第三方 SDK 互操作与真实模型对照

官方 MCP Python SDK 独立客户端校验命令：`python -m evals.s2c_mcp_sdk_verify`。使用官方 `stdio_client` 和 `ClientSession` 启动现有独立 MCP 子进程，走初始化、list_tools、成功调用和失败返回，独立于自研 StdioMCPClient。CI 添加该互操作校验，并将 `mcp>=1.20,<2` 加入开发依赖。该证据覆盖特定 SDK 版本区间和 stdio 单工具子集，不等于 MCP 完整规范符合性。

真实 Qwen 对照脚本：`RUN_S2C_LIVE=1 python -m evals.s2c_live_benchmark --repeats 2`（Windows PowerShell 在命令前用 `$env:RUN_S2C_LIVE='1'`）。两种策略 all/dynamic 都调用真实供应商及原 AgentLoop，复测多工具、隐含意图、参考工具负例、禁用 shell 等 8 种场景，交错策略顺序，统计任务检查通过率、Tool Calls 和中位耗时。输出 `evals/results/s2c/live-comparison-v1.json`，不写入用户原始问题或完整模型回答；需要真实服务配置，CI 不替代用户本地运行。此脚本的有限关键词判分还需人工抽样核对，不能宣称等于真实任务质量或统计显著结论。

在本地真实运行并收到证据前，S2-C 保持 **IN PROGRESS**；不得仅凭 SDK / CI 成功声称 Qwen all/dynamic 实验已完成。

## 真实 Qwen 对照评测的数学歧义定向修复

用户提供的 v1 真实 Qwen 报告共 32 次 Run：all 14/16、dynamic 14/16，失败均在 `implicit_math`；四次失败都成功调用 `calculate` 且最终状态 completed。**这不足以断定是 Skills 误过滤，也不能断定模型算错**：旧题目“18 加上 24 再乘 3”有括号/运算优先级理解歧义，旧版报告未保存算术工具表达式与结果，导致无法诊断。

`evals/s2c_live_benchmark.py` v2 将单个数学歧义题拆为三项：`math_explicit`（先加再乘，结果 126）、`math_precedence`（按运算优先级，结果 90）和 `math_ambiguous`（应先要求澄清而非擅自计算）。前两项分别检查工具成功、**实际工具返回数字**与答案数值；后一项检查未调用算术工具并提出澄清。新增 `checks` 细项与仅包含算术表达式、数值、错误码的诊断记录，不写入记忆/参考工具的原始观察或模型全文。歧义澄清自动判分仅为启发式，必须结合人工判断。

回归执行：`python -m pytest -q`；真实 Qwen 定向复验：`$env:RUN_S2C_LIVE='1'` 后 `python -m evals.s2c_live_benchmark --math-only --repeats 2`；完整对照 `python -m evals.s2c_live_benchmark --repeats 2`。默认写入 `evals/results/s2c/live-comparison-v2.json`。本阶段**没有在 CI 中远程运行用户的 Qwen 服务**，等待本地上传真实报告之后复核，暂不将 S2-C 关闭。
