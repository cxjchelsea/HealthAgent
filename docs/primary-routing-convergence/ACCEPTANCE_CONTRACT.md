# HealthAgent Primary Routing Convergence — 冻结验收合同 v1.2（实施前冻结版）

对应基线 `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`。冻结对象是架构不变量、证据与安全边界，不冻结示例句子或模型提示词。合同变更须单独记录原因及影响，不能通过弱化断言使原本失败的实现“通过”。

## 路由类型与版本合同（冻结）

- 版本：`schema_version="primary-route-v1"`；运行时必须对未知版本、未知枚举拒绝，不得隐式别名容错。`route_kind` 是唯一且不可变的本轮主语义操作标签，`admission_result` 是独立的 Host 裁决，不得用其覆盖前者。
- **冻结规范枚举**：`plan_create`、`plan_revise`、`plan_execute_first`、`plan_resume`、`plan_cancel`、`plan_missing`、`ordinary_tool`、`respond`、`clarify`、`clarify_response`、`reject`。`plan_missing` 表示用户引用已保存计划但 Host 绑定确认缺失的非执行路由；不允许仅凭模型猜测即视为已证实的缺失。`reject` 是无可执行提案的明确拒绝状态。
- **既有 M5 映射（只允许在版本边界的单一 adapter 中显式处理）**：`plan_create→plan_create`，`plan_execute_first→plan_execute_first`，`plan_resume→plan_resume`，`plan_revise→plan_revise`，`plan_cancel→plan_cancel`，`plan_missing→plan_missing`。旧 `other` **不得**自动映射为 `ordinary_tool`、`respond` 或 `clarify`；必须通过 Primary 产生新版本的明确类型，无法确定则 `clarify` 或 `reject`，不得调用旧模型补判。
- **旧草案字符串映射**（仅迁移 adapter，禁止测试随意使用别名）：`create→plan_create`，`revise→plan_revise`，`execute_first→plan_execute_first`，`resume→plan_resume`，`cancel→plan_cancel`；`ordinary_tool`、`respond`、`clarify`、`clarify_response` 在旧 M5 没有同名合同支持，不得伪称已有一一对应。测试分别覆盖新旧版本、拒绝未知值以及 `other` 不自动降级。
- `plan_create` 只保存不执行；`plan_execute_first` 不扩展到其他步骤；`plan_resume` 的范围由提案证据与 Host 信任状态核定；`ordinary_tool` 的具体工具仍由 AgentLoop 选择，不能借此绕过 Host 执行模式授权。

## AC1 单一主语义所有者

**要求**：每个 Unified Turn 在准入路由层仅有**一个主语义所有者**，输出一个 `primary-route-v1` 的 `route_kind` 与绑定证据。允许同一 Primary 内部有限 schema 重试，不允许第二个独立模型重解释、更改或补判路由。`UnderstandingService` 与 `decide_execution_policy_detail` 必须并入同一次 Primary（可复用代码但只产生一份主语义提案），否则在 Unified 路径上按第二主语义裁决计，AC1 判失败。Primary 不得因缺少支持的类型而调用 Legacy 分类器。

**范围排除**：AC1 只约束**本轮准入路由**；AgentLoop 在 Host 批准后开展的多轮工具选择、Observation 驱动决策与回答生成不算第二个主语义所有者，但不得反向修改已批准的 `route_kind` 或扩大 `admission_result`。

**证据**：每个 Turn 保存 `proposal_id/schema_version/route_kind/semantic_attempts`（只存无敏感信息的必要元数据），绑定当前轮快照 ID；证明唯一主语义所有者的调用计数、穷举路由、无效提案和跨状态测试。

**失败条件**：多个模型能改变同一轮主路由；`UnderstandingService` 或 `decide_execution_policy_detail` 在 Primary 之外继续担当独立意图/执行路由裁决；旧分类器作为补判路径。

## AC2 Unified 旧分类器零调用（明确名单）

**要求**：以下 13 个**现有函数的模型调用**在 Unified 正式执行路径上必须为零，调用计数须按函数标识与来源登记，不能靠重命名逃避：

`review_plan_cancellation`、`propose_first_route`、`review_plan_intent`、`check_plan_edit_intent`、`classify_pending_reply`、`review_pending_reference_lookup`、`review_unsupported_capability`、`review_read_only_clarification`、`reconcile_execution_policy`、`review_plan_creation_reference`、`review_missing_plan_reference`、`review_plan_execution_authorization`、`review_plan_execution_scope`。

- `review_plan_revision` 若保留，只可在**已冻结 `route_kind=plan_revise`** 后做步骤/参数/来源绑定；不得改变 `route_kind`、不得在意图未定时运行，也不得以 `intent` 输出反向修改主语义。更好的实现是拆成纯绑定函数。
- `UnderstandingService`、`decide_execution_policy_detail` 按 AC1 计量，不得作为隐藏的第二主语义分类器。
- `propose_new_plan_route` 与 `propose_saved_plan_route` 属于待统一迁移的现有 M5 提案组件，不在上述“Legacy 十三项”内，但统一合同落地后不得以多个独立模型提案竞争主路由。
- Legacy 模式可显式保留旧函数；Shadow 可在持久化以后另行运行，但无授权权力。Unified 中异常、无效提案、权限拒绝、pending、保存计划/无保存计划均不得回退上述函数。

**证据**：对上述每一个函数使用 fail-on-call 探针，配合覆盖正常、异常、澄清、保存计划、缺失计划、取消、修订、权限拒绝、重放的测试；AST/调用图验证绕过函数名改造的间接调用；运行时按角色计数并与 `main@41d91a3...` 只读基线对照。必须同时证明成功路径未被大面积拒绝。

**失败条件**：名单任一函数在 Unified 主执行路径实际被调用；改名、包裹或复制模型分类逻辑来规避；因提案不确定而改走 Legacy。

## AC3 一次逻辑 Host Admission、只能收窄

**要求**：对每个 Primary 提案在一个集中的确定性准入过程中输出唯一 `admission_result`（allow/deny/clarify/restrict）及原因。一次是**一次逻辑决策**，允许内部多个独立的 CAS、schema、策略、权限、引用、恢复检查；不可用第二个模型替用户决定意图，也不得超出提案范围授权。

**证据**：proposal→admission 的不可变映射记录；deny/restrict 测试；跨会话引用、版本冲突、参数来源缺失时拒绝且无副作用。

**失败条件**：Host 修改 route_kind、增加权限、改写引用、将模型不确定映射为可执行工具；拒绝后重入执行链。**工具已有副作用、Dispatch 结果未知、或 Turn 已提交以后，禁止再次调用旧分类器、改走 Legacy 或改写同一 Turn 的语义路由**。必须按原始授权记录终结、补偿或进入人工核对状态。

## AC4 所有执行路径共享副作用与证据治理

**要求**：`AgentLoop`、计划步骤、pending 参数续执行使用同一权威执行围栏合同；不同工具类别可使用不同执行器，但审批、授权范围、幂等、持久化 Journal/CAS、错误终结和重放不得互相矛盾。当前 `apps/api/main.py` 约 687–726 行的 pending 续执行直连 `execute_tool` 必须迁入同一围栏，即使不经过 AgentLoop 也不得免检；执行前绑定原始授权请求、当前回复、具体工具及参数，拒绝重复写入。

**证据**：pending create_task 与 MCP 续执行回归；并发同 request_id 只允许一次副作用；崩溃中断状态必须有明确重入/拒绝路径；审计链可追踪 proposal→admission→dispatch→result。

**失败条件**：绕过执行围栏的直连写入；未知结果被标记为成功；重放产生二次副作用。

## AC5 兼容与安全、真实模型验收

**要求**：保留 S25-01/02/03/04 已验证的核心能力：新建与修订保存计划、FIRST/RESUME/CANCEL、普通工具、动态选择、权限和记忆、未知目标引用、MCP 故障、S25-03 durable evidence。所有自动化回归与至少一组独立的真实模型物理验收通过；需要新增从未用作提示词微调的挑战样本。

**证据**：与 main 基线的差异报告、自动化全量 CI、实际 Qwen/Proxy 端到端结果、拒绝与重放负样本、头 SHA 绑定。

**失败条件**：单纯将旧路径改成全部拒绝以换取零调用；忽略旧功能成功率；用已知失败句子直接写入 Prompt 后重跑同组就宣称泛化。

## 合并门禁

1. AC1–AC5 全部有可复验的自动化或物理证据；失败项不得标为完成。
2. PR 精确 HEAD 的 CI 与 main/rebase/保护条件核验通过，合并前重新验证。
3. 只在获得明确 PR 合并授权后使用 Standard Merge Commit。
4. 任何兼容模式或实验开关保留时，写明路由归属、默认值、退出条件，避免新旧语义双写。

## 实施前基线（2026-11-03，已完成采样）

- 已完成三轮真实模型 HTTP 函数入口采样：10+11+2=23 个请求，AC2 十三项合计 15+11+2=28 次旧调用。统计是 Python 入口调用次数，不等于底层 HTTP 模型调用次数；不是全部执行分支的穷举。
- 证据文件：`primary-routing-main-runtime-baseline.json`、`primary-routing-main-saved-plan-pending-baseline.json`、`primary-routing-main-pending-verified-baseline.json`，全部对应 `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`。
- 关键样本：CREATE/FIRST/RESUME/REVISE/CANCEL 成功触发；Pending MCP 先落库 `needs_input:mcp_reference_lookup`，恢复后 `verified_clarification_execution`；已有计划时 ordinary 请求 `unresolved_tool_failure` 属于已知失败。Pending 的 `create_task` 写入恢复、PostgreSQL/崩溃/并发重放尚未由本次采样证明，属于 AC4 必测项目。
- 冻结结论：本合同 v1.2 为实施前基线；任何改动按合同变更流程单独记录。