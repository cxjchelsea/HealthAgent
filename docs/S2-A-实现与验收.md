# S2-A — 实现与验收

> **阶段状态**：**CLOSED**（按 S2-A 已约定的最小非医学 Agent Loop 范围）。已合入 `main`；不代表 S2 全部完成，也不代表生产级可靠性。
>
> **阶段规划**：[S2-通用Agent实施范围](S2-通用Agent实施范围.md) ｜ **当前进度**：[09-开发路线图](09-开发路线图.md)

## 1. 阶段目标与边界

以 **LangGraph + 模型原生 Tool Calling + 版本化 Prompt** 实现不依赖关键词固定工作流的最小自主工具 Agent：选择工具、结构化校验、执行、观察后继续决策、最终回答及可检验终止状态。

不进入医学领域，不接真实患者数据；首批任务工具为 Run-local 模拟操作。

## 2. 已实现功能与代码落点

| 功能 | 代码 / 接口 | 实际能力 |
|---|---|---|
| 版本化提示词 | `agent/prompts/s2a_core_v1.md` | 系统指令、工具使用、观察低信任与不伪造结果；`s2a-core-v1` |
| 自主执行图 | `agent/loop.py` | LangGraph `decide → tools → decide`；默认最多 6 次模型决策、单轮至多 4 个工具；完成或预算阻断 |
| 原生模型适配 | `agent/decision.py` | Ollama Proxy `/chat` 与 OpenAI-compatible `/chat/completions` 原生 `tool_calls`，无关键词假决策兜底 |
| 工具安全 | `agent/tools.py` | `calculate`（AST 算术白名单）、`utc_time`、`create_task`、`list_tasks`；Schema 验证，任务只在当前 Run 内 |
| 执行与追踪 | `POST /agent/runs` | 最终 JSON 含状态、回答、模型决策轮数、脱敏 Trace；仅显式验收模式保留内存中的完整审计 |
| 网页人工入口 | `apps/web/index.html` | S1 普通聊天 / S2-A Agent 切换；查看答案、决策、工具名和成功状态 |
| 评测 | `evals/s2a_benchmark.py`、`evals/datasets/s2a-20-v1.jsonl`、`tests/test_s2a_*.py` | 20 条真实模型任务、工具参数与结果验证、失败反例、真实 UTC 动态依赖 E2E |

## 3. 验收证据与复现方式

| 验证层 | 已有证据 | 结论边界 |
|---|---|---|
| main CI | S2-A PR #2：https://github.com/cxjchelsea/HealthAgent/pull/2；Standard Merge Commit `dc253d2433ad7a95a0266ceb6c67622854e33a9a`；main GitHub Actions Run `39213450601` SUCCESS | pytest、Smoke、离线评测、编译、限定 Ruff/mypy、PG Alembic 门禁通过，不是全项目严格静态验证 |
| 用户 Windows 回归 | `.venv` 下 `35 passed, 3 skipped`；实时模型用例显式开启 | 证明本机阶段回归，skip 不应算通过 |
| Qwen3:14b + Ollama Proxy | 初版 20/20 工具序列；加强判分曾为 17/20；调整不合理约束后真实严格评测 `20/20`，版本 `s2a-evidence-v2` | 当前锁定小任务集的机械执行证据通过；不能宣称通用任务成功率 100% |
| 动态依赖真实 E2E | `test_dynamic_dependency_real_qwen PASSED`（用户 Windows 日志，2.84 秒） | `utc_time` 的真实 HH:MM 经过工具观察用于后续 `create_task` 参数，并校验标题与最终任务状态 |
| 网页人工验收 | 用户截图：计算 25×8、创建及列出任务、UTC 依赖、拒绝未提供 Shell 工具，Trace 正常 | 属人工体验检查，不代表生产 UI 全流程测试 |

### 关键用例：真实未知时间驱动后续参数

`tests/test_s2a_dynamic_dependency.py`：用户不提供当前时间；必须先取得 `utc_time` Observation，再于**后续模型决策**发出 `create_task(title="记录 UTC HH:MM")`；验证实际小时分钟、标题和 Run-local 最终任务一致。

### 测试命令

```powershell
python -m pytest -q
python -m evals.s2a_benchmark  # 20 条脚本模型决策，仅测执行/评测器
$env:RUN_S2A_LIVE="1"
python -m evals.s2a_benchmark --real
python -m pytest tests/test_s2a_dynamic_dependency.py -m live -v
Remove-Item Env:RUN_S2A_LIVE

python -m uvicorn apps.api.main:app --reload
# 浏览器打开 http://127.0.0.1:8000/，选择 Agent S2-A 人工测试
```

离线脚本 20/20 **绝不**写作真实模型能力。真实模型 20/20 是有限样本与机械规则的结果，且未保存完整明文对话。

## 4. 当前限制与未完成事项

| 项目 | 类型 | 状态 / 后续阶段 |
|---|---|---|
| 跨消息、跨 Run 上下文 | **S2-B 待开发** | Agent 每次 `/agent/runs` 独立；S1 聊天历史未接入 Agent Loop |
| 长期记忆与更正删除 | **S2-B 待开发** | 现无可控 Memory Write/Read Policy |
| 模拟任务工具持久化或外部系统对接 | **后续工具增强 / S2-C** | `create_task`、`list_tasks` 仅对本 Run 有效 |
| Skills / MCP / Harness | **S2-C 待开发** | 尚无受控 MCP 执行及动态工具暴露 |
| 显式规划、目标变更后局部重规划 | **S2-D 待开发** | 当前 ReAct Loop 不能冒称完整 Planner |
| Agent SSE 与服务端取消 | **后续体验/可靠性增强** | Agent 返回最终 JSON；“停止等待”只中断浏览器等待，不保证服务端 Run 取消 |
| Token/成本与 Prompt A/B | **后续评测增强** | 当前使用量字段为 unavailable，未建立通用多版本实验平台 |
| 测试范围 | **持续演进** | 执行结果的机械检查不替代完整语义评审、并发鲁棒性或真实生产安全验证 |

## 5. 后续处理方向

S2-A 已按原定最小范围收口，不再为扩展模拟工具或补长期记忆而重新打开阶段。S2-B 首轮优先接通可点击的多轮 Agent Context，再设计长期记忆管理与更正删除；保留 S2-A 的执行回归。

## 6. 归档的验收演进

- **第 1 版**：真实 20 条 `real.json` 实际工具序列 20/20；当时缺参数、最终任务状态和依赖检验。
- **第 2 版**：加入参数、工具 Observation、运行内任务状态和错误解释，首次真实严格结果 17/20。
- **v2.1 判分修正**：S2A-011/012 可在同一轮批量创建已知任务并读取列表；S2A-016 允许含时间信息的描述性标题且核对真实写入状态；仍保留 S2A-015 等跨决策步骤检查。**复测真实 20/20**。
- **最终 E2E**：新增固定时钟离线用例 + 真实 Qwen 未知 UTC 值动态依赖验证，用户本机测试和网页人工体验均通过。

此节保留历史修正理由；以上已通过的旧评测不应覆盖后续新基线的失败事实。
