# S25-07 Step 1 — Observation 驱动重规划：最新 main 代码现状与 Gap 审计（2026-11-11）

**状态：STEP 1 AUDITED / DESIGN NOT APPROVED。仅审计，不修改 runtime，不启用开关，不授权合并。**

## 1. 精确审计基线和资料
- 仓库 `cxjchelsea/HealthAgent`；GitHub branches/main 返回 **`e02845f7c3452ef452efceb20f7b725e5716529b`**；对应 2026-11-11 已合并 PR #48。
- 上位约束：`docs/S2.5-通用自主Agent能力补齐开发策略.md` V2，尤其 S25-07、§4、§5。
- S25-06 边界：`docs/S2.5/S25-06-STEP6-FINAL-COMPLETENESS-AUDIT-20261111.md`；`docs/S2.5/S25-06-INTEGRATION-HANDOFF-20261111.md`；`docs/S2.5/S25-06-STEP6-DEVELOPMENT-CLOSEOUT-20261111.md`。
- 本次静态核验：`agent/loop.py`, `agent/planning.py`, `agent/plan_recovery.py`, `agent/primary_route_v1.py`, `agent/primary_host_admission_v1.py`, `agent/dependency_plan_schema.py`, `agent/dependency_plan_host.py`, `agent/dependency_plan_runner.py`, `agent/tool_registry.py`, `agent/decision.py`, `apps/api/main.py`, `services/dependency_observation.py`, `services/dependency_dispatch.py`, `services/dependency_execution.py`, `services/db.py`；并核对相关测试文件存在性。
- 证据等级：**已直接读代码/文档 + GitHub ref**；尚未对该 Step 1 运行新 pytest、HTTP、真实 Qwen、真实 PG 或故障实验。相关 S25-06 既有测试通过属于**继承证据**，不能当作 S25-07 验收。

## 2. 当前真实执行路径（不能混为一谈）

### A. 普通 AgentLoop / legacy PlanLedger：存在部分重规划
- `agent/loop.py` 的 LangGraph `decide → tools → decide` 形成 Observation 反馈；`_tools` 可处理 `propose_plan / revise_plan / complete_plan_step`；`agent/planning.py:PlanLedger.revise` 实现 revision 检查、保留有效 completed、显式失效和 pending 替换。
- `agent/plan_recovery.py:candidate_for_failure` 是**特定** `calculate` 工具 `unsupported_expression` 的整数幂改写候选；`PlanLedger.apply_verified_recovery` 只有成功且校验通过后版本化，不能称为普遍失败归因/工具替代。
- `apps/api/main.py` 还有 legacy checkpoint 的计划修订和恢复分支；须经正式 API 的现有 Primary / Host 权限执行，不能把独立 helper 视为合法新入口。
- 结论：**旧格式仅有局部能力**，不是针对 S25-06 版本化依赖计划的通用 S25-07 实现。

### B. 版本化 dependency-plan-v1：有可信顺序执行，但尚无重规划
- 创建合同：`agent/dependency_plan_schema.py` 明确 `revision: Literal[1]`，全 pending，2–8 步、有序 ID/DAG，工具只允许 `mcp_reference_lookup@1.0.0` 和 `text_stats@1.0.0`。
- 运行 Host：`agent/dependency_plan_host.py:_step_for_read` 要求 `checkpoint["revision"] == 1`、`pending/completed`、已完成步骤连续前缀，`executions` 与已完成 ID 完全相符。**不是版本化修订解析器**。
- 当前 Primary Host：`agent/primary_host_admission_v1.py` 对该格式 `plan_revise` 和 `plan_cancel` 显式 `deny dependency_control_not_supported`；仅 `plan_execute_first/plan_resume` 在正确事实和开关下允许。不要退回 legacy 控制。
- 实际正式入口：`apps/api/main.py:send_agent_in_conversation` 的依赖格式 CREATE 分支及 FIRST/RESUME 调用 `run_dependency_read_scope`，是在已通过 Primary / Host 之后的已有主链，不应新增第二套 Router 或独立执行 API。
- `agent/dependency_plan_runner.py`：`run_one_dependency_read` 先绑定、Fence、记录 dispatch_unknown，再 invoke Registry 和写 Receipt；`ok=false` 返回 `DependencyReadOutcome(status="blocked", checkpoint=None)`，**不将失败步骤标记 completed**，scope 停止；`run_dependency_read_scope` 使用第一个 pending 的顺序执行，未调用重规划。
- `services/dependency_observation.py` 只把已 checkpoint 的、可信 `definition` 字段绑定到后继参数；`services/dependency_execution.py` 只在成功 Receipt 后原子推进已完成步骤及 TaskState。失败 Receipt / 未完成状态不能冒充已完成 Observation。
- 当前版本化计划不支持新的 revision、替代工具、局部修订或失败后自主选择下一动作。S25-06 的恢复、证据链与并发围栏提供安全基础，但**并不构成 S25-07**。

## 3. Capability Gap 矩阵（按现行正式主链）
| S25-07 能力 | 状态 | 证据、影响及下一步 |
| --- | --- | --- |
| 普通工具 Observation 反馈到模型决策 | 已实现（限普通 AgentLoop） | `agent/loop.py` decide/tools 回环；不能推定新格式已拥有 |
| legacy 计划手工/模型请求修订 | 部分实现 | `PlanLedger.revise`、API legacy 修订；不适配新格式 |
| 特定 arithmetic 失败恢复 | 部分实现 | `candidate_for_failure` 只认 calculate unsupported_expression；非通用策略 |
| 可信成功 Observation、来源绑定、持久化执行证据 | 已实现（S25-06 限定场景） | Receipt/Attempt/Checkpoint、`observation_ref`、`dependency_execution`；只读工具对 |
| 版本化计划失败结果可靠记录 | 部分实现 | 失败先写 Receipt，runner 返回 blocked；**没有失败步骤重规划状态机** |
| 统一失败分类（暂时性/永久性/参数/授权/证据未知） | 部分实现 | 工具与 Host 有错误码；未发现 S25-07 专用跨源决策合同 |
| Observation 触发继续/重试/替代/局部修订/澄清/停止 | 未实现（新格式） | 当前 scope `blocked` 即停止，无可信重规划提案 → 准入 → 修订事务 |
| 新格式计划 revision > 1 与原子修订 | 未实现 | `revision==1` 硬约束，Host 拒绝 plan_revise |
| 保留已完成步骤及已验证证据后修订后缀 | 未实现（新格式） | 当前完成前缀与 execution revision 绑定；必须定义跨版本引用 |
| 证据继承/失效、DAG 依赖重校验 | 未实现 | 当前 observation source_record 要求同 plan_revision；若仅递增 revision 会切断合法历史证据 |
| 多种工具可替换且不扩大权限 | 未实现（新格式） | Schema/Host 只读固定工具对；不得偷偷增加任意工具 |
| 对模型驱动失败归因的真实 Qwen E2E | 证据不足 | 旧的 S25-06 CREATE/FIRST/RESUME 6/6、M04 8/8 不能代替失败重规划 |
| 动态变更目标或中途约束的通用重规划 | 证据不足/部分 | S25-01 目标更正与 legacy API 路径存在；新格式与约束变化关联需新合同和测试 |
| PG 并发 CAS、Crash 与未知结果 fail-closed 基础 | 已实现（限定情况） | S25-06 的已记录物理测试；修订竞态尚无新增验证 |

## 4. 主要架构风险及审查门槛
1. **双版本语义混用（P0）**：`PlanLedger` 的 revision/mutation 逻辑不能直接套用 `dependency-plan-v1`；后者 Host 当前明确 revision=1、前缀完成、严格 execution→revision 绑定。混用会绕开 Host/丢证据。
2. **失败结果与未知结果混同（P0）**：有可信 `ok=false` Receipt 与缺失 Receipt/worker crash 的 `dispatch_unknown` 完全不同。只有证明失败且具备重试权限的场景才可考虑重试；未知结果不自动 replay。
3. **跨 revision 证据指向（P0）**：已 checkpoint 的 step 和 Receipt 不随修订改写；需显式定义 `origin_plan_revision` / preserved-prefix evidence 绑定、不可变来源与失效边界。切勿简单把旧 executions.plan_revision 整体改为新数字。
4. **并发及写后冲突（P0）**：重规划写入与 FIRST/RESUME 的 Claim、generation Fence、Turn CAS、step checkpoint 必须串行或有可证明的乐观并发门禁；任何并发修订须防止“旧计划继续派发”。
5. **模型语义与 Host 权限混层（P0）**：Primary 可以解释失败和提议下一步；Host 校验合法状态/权限/工具版本/来源，不承担语言语义等价判断。S25-06 留下 1 xfailed，不能误称自动修订已解决。
6. **仅有静态审查不能确认完整用户体验（P1）**：具体失败 Receipt 是否对下一轮模型可见、是否能稳定解读，必须在 Step 2/5 通过实际 HTTP/PG/Qwen 核验。
7. **扩大工具集（P1）**：S25-07 先在受信只读依赖计划上实现最小闭环；添加任意 read/write、动态拓扑、医疗工具会把阶段扩大成 S25-05/06/12/13 的重做。

## 5. 建议 S25-07 限定开发范围（Step 2 待用户审查）
### 必须
- 最小可信失败事件输入：区分 verified-success / verified-failure / unknown / admission-denied / invalid-arguments / conflict，保留 dispatch、receipt、attempt、step、revision 及来源 ID；错误文字不能授予执行权。
- 基于既有 **Primary** 而不是新 Router 的结构化 `ReplanProposal`（仅提议，动作可为 continue/retry-on-verified-failure/replace-read-step/revise-pending-suffix/clarify/stop）；明确不允许模型覆盖 completed evidence。
- 由既有 **Host** 校验 proposal、旧 revision/CAS、已完成前缀、后继依赖、工具版本、权限与执行围栏；提交新的计划 revision 与 revision event/证据引用，不能无依据复制完成状态。
- 在当前白名单只读工具对内做**有限且可实际触发的**失败→重规划→安全继续 E2E；避免为“替代工具”制造不现实的测试或硬编码自然语言。
- 对不可安全修订/未知结果 fail-closed 暂停，确保诊断可见且不重放。
- 自动化测试涵盖重复请求/重复 Receipt、并发修订、旧 revision 调用、已完成 evidence 保留、失败和未授权工具、DAG 校验、模型输出异常及用户目标不匹配案例；正式 API + PG + Qwen 实验单独记录。

### 有意不做
- S25-08：任何目标的普遍语义满足证明、最终成果质量与交付闭环（但本阶段不得把失败误报成功）。
- S25-11：生产级 worker-death attestation、全局灾难恢复、跨外部系统 exactly-once（但本阶段必须沿用未知执行围栏）。
- S25-16：完整用户主动取消、暂停、撤销与复杂审批交互（保留当前控制拒绝，必要的澄清/安全停止可做）。
- S25-12/13：任意副作用工具的授权与生产安全体系；现阶段只读白名单。
- S25-18：全局 Benchmark；只做 S25-07 的专项评测集，向后继 Benchmark 交接。
- 不因 S25-06 既有 xfail 擅自重开 S25-06 收口；但若发现新重规划变更可绕开目标/权限防护，视为当前阶段 P0 blocker。

## 6. 最小渐进架构建议
```
Current HTTP endpoint
  → existing Primary (interpret request and trusted observation summary)
  → existing Host admission (proposal only, never auto-authorize)
  → existing Plan/Dispatch/Receipt/Attempt/Checkpoint
  → verified failure / mismatch event
  → Primary ReplanProposal [new decision type, not new router]
  → Host validate revision + owner + evidence + active claim/fence + tools + DAG
  → atomic revision / preserve proven completed prefix (immutable evidence refs)
  → existing guarded execution on next authorized turn
```
更建议“先写出修订且保存，下一次明确授权后执行”的可审计两阶段方案，而非失败时在同一 Turn 无限制自动连续重试。若希望完全自动 ReAct 式重规划，Step 2 必须事先定义 Host 授权、预算和失败后的继续控制范围，不能默认放开。

## 7. 接口复用及阶段依赖
- **S25-01/02**：`primary_route_v1.py`、`loop.py`；语义理解和 model decision，不另设 classifier。
- **S25-03**：`AgentTaskState / AgentAttempt / AgentStateTransition` 及 `services/task_state.py`，revision 事件与可审查证据需一致。
- **S25-04/05**：`READ_REGISTRY`、version-pinned Host grants；替代工具必须先合法注册/暴露/授权。
- **S25-06**：`dependency-plan-v1`, `dependency-plan-read-v1`, `dependency_plan_host/runner`, `dependency_dispatch/execution/observation`, Fence/Claim/CAS；避免无意改动已有 2–8 步正向合同。
- **S25-08/11/16**：只定义接口与拒绝/交接，不提前实现最终验证、全面恢复或主动任务控制。

## 8. 下一步建议（严格按六步）
1. **Step 2 行为合同**：定义 8–12 个可实现的正反例；关键决策为“自动重试的授权边界”“只读有限工具替换是否可在现有两工具上真实证明”“失败后的下一轮如何触发重规划”。
2. **Step 3 技术设计与独立复审**：精确 DB mutation、revision / evidence provenance、API 主链接线、Claim/Fence 竞态以及 migration 兼容；设计通过前不得进入 Step 4。
3. Step 4 可按 Host 失败证据 → 原子 revision → Primary 接线 → 受控执行切片渐进开发；**现在未获准实现**。
4. Step 5: targeted pytest、完整回归、正式 HTTP、真实 PG 并发/崩溃、真实 Qwen 未见任务、负向/对抗数据。
5. Step 6：独立审查并获得用户明确 `AUTHORIZE MERGE #N` 才合并。

**审计结论：S25-07 有可复用基础但尚无版本化依赖计划的 Observation→Host-authorized revision→trusted resume 完整闭环。Step 1 已形成静态能力 Gap；S25-07 本身未完成。**
