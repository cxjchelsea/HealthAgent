# S25-05 Step 6 — PR #33 第二轮代码差异复审

审查日期：2026-11-09
审查 HEAD：`0e4f4df53c272ef38ffccf590fe71b6140f1c284`
Base：`main@b2a17593ec062b030933e25ca7a35a1f6025701b`
审查对象：PR #33（Draft，未合并）
审查方式：通过 GitHub PR **全量 changed-file 列表和 per-file diff** 追溯代码变化；对关键生产模块读取 HEAD 完整实现片段；交叉核对测试与 GitHub Actions。**此文属于同一助手的第二轮代码复审，不是另一名独立审阅者的 GitHub Approving Review，也不是形式化安全验证。**

## 1. 审查覆盖与证据

- PR diff：33 个变更文件；`main...` 比较结果累计 143 提交、约 3718 行更改（收口时的 Compare 快照）。重点深审了 `agent/loop.py`、`agent/primary_route_v1.py`、`agent/tool_registry.py`、`agent/tool_invoker.py`、`agent/plan_read_invoker.py`、`agent/plan_tool_versions.py`、`agent/host_plan_execution_binding.py`、`agent/mcp_stdio_client.py`、`apps/api/main.py`、`services/evidence_store.py`、`services/plan_dispatch.py`、`services/m4_offline_recovery.py`、`alembic/versions/0019_tool_versions.py`，并核对对应测试。
- 精确 HEAD GitHub Actions [42584320474](https://github.com/cxjchelsea/HealthAgent/actions/runs/42584320474)：**1271 passed、5 skipped**；PostgreSQL 定向 **6 passed、2 skipped**；全部 job success。
- 上一阶段真实 `ollama_proxy/qwen3:14b` 13 场景 × 两轮 **26/26 PASS**，median 6070.5 ms；使用隔离 SQLite 与受信本地 MCP mock。原始 JSON 保存在用户本地 `artifacts/s25-05-step5-final-full.json`，并未写入此仓库。
- GitHub 现有 review submissions：0；inline threads：0。无独立第三方签名的复审记录。

## 2. 发现清单（按优先级）

### P0 — 0 项已证实

未在复审范围中发现有证据支撑的直接授权绕过、未经 Host 准入写入或 M4 无日志派发。**不能据此证明所有 P0 均不存在**。

### P1 — 0 项已证实

未发现需要阻断合并的、已有复现依据的高严重度缺陷；权限、执行与存储边界的已知回归均有对应测试。

### P2-01 — Primary 有界重试的触发条件比设计合同宽

- **位置**：`agent/primary_route_v1.py` 中 `propose_primary_route`，条件 `any(item["category"] == "primary_route_pending_read_invalid" for item in issues)`。
- **事实**：只要前 8 条安全的 Pydantic 错误中含该错误，即使**同时还有其他独立 Schema 错误**，仍会触发第二次完整模型生成。设计/文档表述是“仅在特定 Pending read 字段冲突时”触发；实际是“含该冲突时”触发。
- **影响**：增加一次模型调用、延迟和诊断歧义；**不是授权绕过**，因为第二次输出仍会进行完整 `RouteSignal` 校验、quote 来源绑定和 Host 准入；不成功则拒绝。
- **建议**：若要严格实现“仅一种错误”合同，将该条件改为“错误集合精确等于唯一 `primary_route_pending_read_invalid`”，并新增同一提案兼有 `schema_version` 缺失及 Pending 冲突时**不得重试**的回归。此改动改变模型调用策略，应独立测试后再合并。**此复审只记录发现，未修改代码**。
- **优先级**：P2，建议合并前收紧或由维护者明确接受为有限调用成本债务；不应在没有真实模型复验时进行大规模路由重构。

### P2-02 — 版本兼容范围是 Legacy 执行器，不是自动迁移承诺

- **位置**：`agent/plan_tool_versions.py`、`agent/plan_read_invoker.py`、`services/plan_dispatch.py`、`alembic/versions/0019_tool_versions.py`。
- **事实**：完全未版本化的历史 Checkpoint 仍允许在当前 `legacy-plan-v1` 执行器兼容条件下运行；新计划步骤/Dispatch 显式标记 `legacy-plan-v1`，历史 Attempt/Dispatch 的版本可为 NULL。已有回归覆盖版本不匹配、混合、篡改和升级后的拒绝。
- **影响**：未来修改 Legacy Adapter 语义时必须同步更新版本常量并隔离旧计划；不能宣称旧数据已自动转换或所有 Attempt 都有具体版本。
- **建议**：作为后续版本升级的必查项，并在发布变更说明保留。**不建议为了本期 05 补写历史版本**。
- **优先级**：P2/已知设计债务，当前兼容策略下不构成已证实功能错误。

### P2-03 — 受限模型重试仍增加成本与延迟

- **位置**：`agent/primary_route_v1.py` 的 Schema 冲突重生成、`agent/loop.py` 的 ordinary read 无 Observation 重决策。
- **事实**：MCP 自然语言调用在最后两轮测试中每轮记录 **5** 次 HTTP model attempts，显式为 **4** 次；整体端到端模型耗时中位数为 6070.5 ms。
- **影响**：额外推理资源消耗；无多模型/线上负载样本，不能推断生产 P95 或长期稳定性。
- **建议**：后续观察实际触发比率、延迟分布、同轮重复调用的具体原因。不得撤销严格 Schema 或 Host 执行校验来换取提速。
- **优先级**：P2/性能债务。

## 3. 关键实现的代码级裁决

| 边界 | 代码观察 | 裁决 |
| --- | --- | --- |
| Host → Registry → Invoker | `approved_read_grants` 仅 allow + ordinary_tool + read_only + opt-in 才授权；Registry 还检查 enable/grant/exposure/参数/version；Invoker 仅重核验，不签发权限 | 符合 Step 4/5 的已实现合同 |
| ordinary tools | `AgentLoop` 在已授权普通只读请求禁用旧“保存的”记忆早返回/重试，并要求工具 Observation；失败会维护 unresolved failures 为 partial，不伪造成功 | 针对既有回归充分覆盖；注意该门禁是特定执行模式策略 |
| saved plan | `PlanReadInvoker` 校验只读工具、严格参数、版本、guard，再 Journal begin、工具调用、receipt；Host 的 claim、owner、checkpoint CAS 不由 Invoker 替代 | 无已证实 M4 权限回退 |
| write/Pending | 普通只读工具不能获得 create_task/save_memory；统一 HTTP 写入仍在受 Host 约束的入口 | 已审查核心调用路径；非全系统形式化证明 |
| MCP | `StdioMCPClient.call_trusted_reference` 启动固定本地模块，校验 serverInfo、固定单工具目录及参数 Schema 后执行 `tools/call`；不是通用外部 MCP 信任系统 | 本阶段受信 mock 有效，不宜直接声称第三方 MCP 安全 |
| attempts/version | 普通 Registry Attempt 版本 `1.0.0`，Plan Dispatch `legacy-plan-v1`；prepared→terminal 校验版本相同；NULL 明确是历史未知 | 符合已确定兼容策略 |
| migration | 0019 只增加两个 nullable tool_version 字段，未默认回填旧记录；CI 检查 Alembic head | 无发现迁移回填错误 |
| regressions | 普通/显式读取、MCP、拒绝 Shell、保存计划及 PostgreSQL 崩溃、并发测试均有现有证据 | 通过当前验收范围；生产与外部服务不在范围 |

## 4. 合并门禁与下一步

**代码复审裁决：CONDITIONAL APPROVAL FOR ENGINEERING CLOSEOUT（技术复审有条件通过），不是 GitHub Approved Review。**

- [x] 代码/授权关键边界二次核对，无已证实 P0/P1。
- [x] 精确 HEAD CI 通过，完整真实模型 26/26。
- [x] P2-01/P2-02/P2-03 已具体验证、分级并记录。
- [ ] 合并前明确 **P2-01** 是收紧测试并验证，还是由维护者显式接受当前触发宽度。
- [ ] 请独立维护者或第二人审阅 PR（GitHub 当前没有 review）；必要时对 143 个提交的范围拆分审计，而非假定这一轮替代同行审查。
- [ ] 仍需用户对 PR #33 的显式合并授权；保持 Draft，未合并。

本次复审**未引入新的生产逻辑修改**。若处理 P2-01，应以最小代码+定向单元测试+最新 HEAD 全量 CI 为准。
