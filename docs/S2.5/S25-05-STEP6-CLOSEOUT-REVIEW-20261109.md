# S25-05 Step 6 — 最终收口审查与 PR #33 合并门禁

审查日期：2026-11-09
审查对象：PR #33，`feat/s25-05-step4-tool-registry-slice-20261106` → `main`
审查基线：`706e16d40673b6334a910f9014161b9804dcb483`
范围：本次仅审查、归档和提出门禁，不扩大 S25-05 的实现范围，不改变生产授权策略，不合并 PR。

## 1. 结论

**Step 6 技术收口审查：已完成。建议转入独立 PR 复审准备；不建议在缺少明确合并授权和独立复审的情况下直接合并。**

- Step 5 本地真实 `ollama_proxy / qwen3:14b`、Unified、隔离 SQLite、受信本地 MCP 目录：完整测试 **26/26 PASS**（13 场景 × 2 轮），中位耗时 **6070.5 ms**，见 `S25-05-STEP5-LIVE-ACCEPTANCE-20261109.md`。原始 JSON 保留于用户本地 `artifacts/s25-05-step5-final-full.json`，**未提交到 GitHub**，不能声称独立拉取可重现原始报告。
- 最终审查 HEAD CI [42583607754](https://github.com/cxjchelsea/HealthAgent/actions/runs/42583607754)：`1271 passed / 5 skipped`；PostgreSQL 定向 `6 passed / 2 skipped`；全流水线成功。CI 不包含本地真实 Qwen/Ollama Proxy。
- 本 PR 相对 `main` **ahead 143 commits，behind 0，33 changed files，约 3718 changed lines**（本次 GitHub Compare 快照）。这不是一个可按单文件功能补丁审阅的小 PR。
- GitHub PR 当前 **Draft、未合并**；截至审查时 **0 条 GitHub review submissions，0 条 inline review threads**。这说明当前没有可见的独立复审记录，不能将助手静态核对称作第三方审查。

## 2. 安全边界核查

| 边界 | 证据与当前合同 | 裁决 |
| --- | --- | --- |
| Primary 与 Host | `agent/primary_route_v1.py` 产生语义提案；`agent/primary_host_admission_v1.py` 确定性准入；API 先持久化路由/准入证据，再进入执行。未通过 Schema 的提案拒绝，不能自动借其他路由执行 | 通过既有单元/HTTP 回归；非形式化安全证明 |
| Registry 与 ordinary Invocation | `agent/tool_registry.py` 每次执行重校验 enabled、Host granted、model exposed、Pydantic 参数、版本 `1.0.0`；`agent/tool_invoker.py` 仅封装调用，不发行授权 | 已审查；只读边界符合本阶段意图 |
| 工具授权 | `approved_read_grants` 只在 Host allow + ordinary_tool + read_only 且对应 feature flag 启用时返回 grant；无法由模型文案或 MCP 发现自行开启 | 已审查、测试覆盖 |
| Saved Plan / M4 | `agent/plan_read_invoker.py` 先校验工具、参数、版本和 guard，再进入 `PlanDispatchJournal.begin`、工具执行、receipt、checkpoint CAS；与普通 `OrdinaryReadInvoker` 分离 | 本阶段兼容迁移通过；不宣称分布式 exactly-once |
| Pending 和写入 | 普通只读授予不会转化为 create_task/save_memory 写入许可；Pending 的 durable origin/confirmation 单独管理 | 已审查既有边界与 PostgreSQL 回归 |
| MCP | Registry MCP 固定本地 `StdioMCPClient`、受信进程及目录比对；没有将外部动态发现的工具提升为授权工具 | 通过当前 local mock 范围；**不包含任意第三方 MCP** |
| Trace / Attempt | 普通 opt-in 只读工具有准备/终结 Attempt、版本 `1.0.0`；保存计划使用独立 Plan Dispatch 与 `legacy-plan-v1` | 实际 26/26 验证；历史字段兼容风险见下 |
| 非调用/拒绝 | `no_tool` 与 `deny_shell` 各两轮无工具调用、无 Attempt、无 Dispatch；Shell 被 Host 限制 | 在本地指定验收集通过 |

## 3. 版本兼容与迁移判断

1. `0019_tool_versions` 为 `agent_attempts` 和 `plan_tool_dispatches` 增加 **nullable** `tool_version`；历史 NULL 明确表示 unknown，不回填当前 Registry 版本。
2. 普通 opt-in Registry Attempt 持久化 `1.0.0`；新建的保存计划步骤固定 `legacy-plan-v1`，M4 Plan Dispatch 保留相同版本。**它们不是同一套工具适配器或可互换授权**。
3. 历史完全无版本的计划仅在 `LEGACY_PLAN_EXECUTOR_VERSION == UNVERSIONED_LEGACY_COMPATIBLE_VERSION` 时可继续交由原 Legacy 执行器处理；混合字段、显式 NULL 降级、版本不一致均拒绝。未来升级 Legacy Adapter 的语义时必须同时更新版本常量并隔离/重新审核旧计划。
4. 完整真实模型报告中 Plan 路径的 `durable_tool_versions` 含 `"None"` 和 `"legacy-plan-v1"`：后者是 Plan Dispatch 版本；前者对应允许为空的旧式 Attempt。**这是已知兼容语义，不得宣称每条 Attempt 都是版本固定的**。
5. 尚未验证未来 Legacy Adapter 升级后对所有历史数据的现场迁移；本次验收不涵盖自动迁移策略。该项应保留为未来版本升级门禁，**不是当前 Registry Slice 的新增功能需求**。

## 4. 重试、延迟和失败处理

- `AgentLoop` 在已授权 ordinary/read-only 请求没有工具 Observation 时，仅给模型一次重新决策机会；仍未观察到工具调用则返回 `ordinary_read_unverified`，不将臆测信息标记为完成。
- `Primary` 仅在 fresh-turn 的 `primary_route_pending_read_invalid` 出现时，由**同一个 Primary 决策器**完整重生成最多一次。没有字段自动修复、没有换路由分类器、没有跳过 schema/quote/evidence/Host 检查；再次不合法继续拒绝。已在 Step 5 真实模型验收。
- 自然语言 MCP 检索两轮的 `model_http_attempts=5`；显式 MCP 为 `4`；这是额外模型调用成本，不能作为性能已优化的证据。
- 历史异常路径：一次根级 `oneOf` 输出 Schema 曾使真实模型 MCP 定向 0/4，已撤销且完整 26/26 再验证通过。不得在没有同等真实模型证据时重新引入该约束。
- 新的自动恢复机制是有界的，但**未来若要求严格一次模型请求/轮的成本合同，需要重新评审**；不能把当前方案描述为单调用 Primary。

## 5. CI 与验收边界

- 当前 GitHub Actions：全量 pytest、M4/PostgreSQL 并发与崩溃测试、Alembic `0019_tool_versions`、既有 benchmark、选择性 Ruff 与单模块 Mypy。
- **不是**全仓严格 Mypy/静态安全审计；不是生产负载测试、外部 MCP 风险审查或医疗事实正确性评估。
- 完整 26/26 是真实 Qwen + 本地隔离环境验证，而不是可证明通用模型与任意工具组合均成功。
- 验收不改变 `Settings` 的 S25-05 registry opt-in 默认 `False`，亦不批准生产部署。

## 6. Step 6 门禁裁决

| 门禁 | 结论 |
| --- | --- |
| 功能与安全回归 | **PASS**：26/26 实际模型，最新 HEAD CI green |
| 版本兼容边界 | **PASS with documented limitation**：nullable legacy Attempt 和新 Plan Dispatch 双版本语义受约束 |
| 工具授权与执行证据 | **PASS within tested scope** |
| 运行性能 | **ACCEPTED DEBT**：自然 MCP 额外一次模型生成及延迟 |
| 外部 MCP / 生产安全 / 多模型泛化 | **NOT ASSESSED**；不纳入本期功能签收 |
| 独立 PR 复审 | **NOT DONE**：GitHub 当前无 Review |
| PR 合并 | **NOT AUTHORIZED**：继续 Draft，不合并 |

### 建议的后续唯一工作流

先以本审查文档和 Step 5 原始本地报告为依据进行一次**独立 PR diff/风险复审**，重点覆盖 33 个文件、M4 & Pending 授权边界、版本迁移和有限重试。复审不发现 P0/P1 且最新 CI 继续通过时，可以申请用户将 PR #33 改为 Ready for Review / 明确授权合并。**未经用户显式授权不执行合并**。

本审查没有发现需要立即继续进行功能性补丁的已证实 P0/P1；这不是对所有未知缺陷不存在的保证。
