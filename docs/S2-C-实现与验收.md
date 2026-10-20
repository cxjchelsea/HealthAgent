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
