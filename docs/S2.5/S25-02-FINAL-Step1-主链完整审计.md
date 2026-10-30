# S25-02 Final — Step 1 主链调用与风险审计（2026-10-29）

## 0. 证据与判定边界

- 源码基线：`main` 合并 PR #26 后的 `8789c0178a0b4962969068e820b302bd4ca3ea59`；审计分支 `refactor/s25-02-final-mainline-convergence`。
- 本文针对仓库源码的**静态分支审计**，并引用上一阶段已有的 M5 11/11 live + SQLite 34/34；**不是**对新的默认模式的真实 Qwen 验收。
- 代码坐标为基线文件的行号，后续修改后以 diff 及 commit SHA 为准。
- 本步 **只审计，不修改生产运行模式**；判断分为「已确认的路径事实」「需要设计收敛的风险」「需要动态复验的疑问」，不把可疑调用点直接称为安全漏洞。

## 1. 可信数据与状态边界

| 源码位置 | 事实与责任 | 分类 |
|---|---|---|
| `apps/api/main.py:177-223` | 校验会话归属、request_id 去重；在同一事务写入 Message 与 running Turn，读取最新 pending clarification | **保留** Host Turn / Identity |
| `apps/api/main.py:224-243` | 同事务捕获前置计划快照、pending 来源和 Turn；M3 捕获仅在 shadow 或启用 M5 + guarded + structured 等条件下开启 | **设计审查**：切换默认时不应丢失捕获 |
| `apps/api/main.py:244-251` | 并发运行 Turn 的数据库唯一约束失败返回 409 | **保留** 并发约束 |
| `apps/api/main.py:257-270` | 保存计划执行前，依据原始快照执行 durable claim | **保留** 安全关键路径 |
| `apps/api/main.py:1081-1155` | 在 Turn 终结事务中保存计划 checkpoint、取消 CAS、Dispatch checkpoint、释放 claim、写审计状态 | **保留** 原子持久化；外部副作用不在本事务内 |
| `apps/api/main.py:1251-1276` | recovery-status 显示未决 Dispatch、未知结果及人工检查状态；无自动 replay 权限 | **保留** 恢复边界 |

Host 的数据库快照是计划存在性、revision、已完成状态、dispatch 和 pending 原因的事实来源。所有「模型说需要执行」的结果均不得越过上述边界。

## 2. 当前语义调用链（实际有条件的主路径）

```text
POST /conversations/{cid}/agent/messages
  ├─ Owner + request-id replay gate, Turn creation, pending + plan snapshot capture
  ├─ goal_confirmation supplied? -> host-verified goal confirmation
  ├─ no saved plan + no pending + guarded/structured/M5 opt-in
  │     └─ propose_new_plan_route -> plan_only | other | fail-closed
  ├─ saved plan + no pending + guarded/structured/M5 opt-in
  │     └─ propose_saved_plan_route -> FIRST | RESUME | REVISE | CANCEL | other | fail-closed
  ├─ saved-plan CANCEL -> Host cancel admission -> CAS deletion
  ├─ deterministic explicit revision binding conflict fence
  ├─ saved FIRST/RESUME -> independent execution authorization + scope review
  ├─ saved REVISE -> targeted old/new argument extraction -> single-step pending update
  ├─ when no terminal result: structured Understanding / goal clarification /
  │   pending-answer validation / action policy / durable goal proposal
  ├─ if still no terminal result: plan_decision from M5/selected saved route
  │   OR legacy review_plan_intent -> resolve_plan_admission -> Host admission
  ├─ guard against explicit cancel/save-only constraints and recovery uncertainty
  ├─ AgentLoop with plan_control or ordinary action
  └─ terminal transaction + optional post-commit M2 shadow diagnostics
```

### 2.1 关键调用矩阵

| 入站状态及操作 | 首个语义所有者 | 后续许可 / 专项复核 | 当前可疑点及处理建议 |
|---|---|---|---|
| 无计划、新建 SAVE-ONLY；M5 模式 | `propose_new_plan_route`，`main.py:307-337` | `build_plan_checkpoint` + `admit_guarded_plan_create` (`:795-831`) | 主语义已唯一；保持来源和保存不执行围栏 |
| 无计划、普通工具；M5 模式 | M5 no-plan 的 `other` | 通用 Understanding + policy + AgentLoop (`:544-723`, `:1013-1021`) | **需要清晰界定：**通用 Understanding 是目标/约束理解还是重复主意图；不能把其工具请求无条件重新解释为 SAVE-ONLY |
| 无计划、请求执行不存在的旧计划 | M5 no-plan `other` | legacy `review_missing_plan_reference` 在特定 plan route 下 (`:777-784`)；通用理解/工具 | **需负向动态用例**，确认不存在可被普通工具意外执行的路径 |
| 已有计划、FIRST/RESUME；M5 模式 | `propose_saved_plan_route` (`:338-360`) | execution authorization `:844-860`；scope `:868-889`；Host `:952-964`；claim `:257-270` | **保留二次验证作为 veto 而非第二主路由** |
| 已有计划、REVISE；M5 模式 | M5 `plan_revise` | `review_plan_revision` (`:480-540`) / exact old arg + pending status | 保留参数提取，但限定只为主路由已选择 REVISE 服务；验证修改其余步骤不变 |
| 已有计划、CANCEL；M5 模式 | M5 `plan_cancel` | `admit_guarded_plan_cancel` + Host CAS (`:364-395`, `:1087-1100`) | 保留授权与物理取消 |
| 已有计划、ordinary / saved `other` | M5 `other` | generic Understanding / policy / AgentLoop | **需要负向复核**：已保存计划不能让普通工具意外触发任何计划 Dispatch |
| Pending clarification answer | Host pending origin 先行；M5 条件排除 (`:197-203`, `:307-312`) | `classify_pending_reply` `:565-576`；仅对限定工具校验续接 `:653-695` | **保留**：不是新的独立主意图，应通过来源及原操作权限保护 |
| Goal confirmation | verified goal state + expected version (`:286-300`) | GoalStore 更新 `:1139-1142` | **保留** 原事务确认；无条件重新路由是错误的 |
| Legacy/default `direct` | 旧流程/AgentLoop；M5 不触发 | 当前旧兼容策略 | **主收敛待完成**：默认仍不是经 M5 验收的路径 |
| Debug/test injected decider/policy | `agent_decider`/`execution_policy` 自定义路径 | 其测试边界依赖注入 | **必须明确适用范围**；不能将测试捷径误认为真实默认模式 |

## 3. 实际重复语义入口：与必要复核分开

### 3.1 需要收敛或变成明确 Legacy-only 的入口

1. `review_plan_intent`: `main.py:452,466,756`。在 M5 已产生 `PlanRequest(intent="other")` 的条件下，`plan_decision = reviewed_plan_intent or unified_new_plan_decision or ...` (`:755-763`) 会复用 M5 结果，不再调用此函数；**但是**若请求不符合 M5 入口条件（pending、非 guarded、非 structured、注入 decider），旧入口仍可运行。默认改造要把这变成显式兼容边界。
2. `review_plan_cancellation`: `main.py:369`。当 M5 saved route 为明确非空 `other` 时条件表达式不会调用旧取消审核；当未走 M5 或 M5 无候选时才执行旧路径。保留兼容，但不应在新默认独立竞争。
3. `check_plan_edit_intent`: `main.py:498-506`。用于 revision reviewer 不确定时的旧兜底；M5 已明确选 REVISE 时会强制 unresolved，避免第二轮改意图，但没有 M5 判定时仍可能调用。建议仅 legacy-only。
4. `review_plan_creation_reference`: `main.py:770-776`。M5 CREATE 已有确认时不再调用；旧计划创建时仍可能调用。建议 legacy-only。
5. `m5_first_route_enabled` 分支 `main.py:429-462` 是 FIRST-only 前置实验；与 M5 unified 共存时优先 M5。设计冻结时应明确如何退出 FIRST-only 独立所有者。
6. `explicit_deferred_plan_request` / `explicit_saved_plan_cancellation` (`:965-989`) 是拒绝型约束围栏，**不是**可直接删除的关键词意图分类器；任何调整不得允许危险行为回退到普通 AgentLoop。

### 3.2 必须保留的安全/参数复核

- `review_plan_revision` (`:484-540`) 对「改哪步、旧参数、新参数」做专项抽取；`apply_plan_revision` 验证真实待处理计划，拒绝冲突。
- `review_plan_execution_authorization` (`:844-850` 及受注入路径 `:924-940`) 是行动前独立确认；不应当复用 M5 的主语义结果自动放权。
- `review_plan_execution_scope` (`:868-889`) 只有验证通过才能扩大到剩余全部步骤；模糊时不能升级 scope。
- `admit_guarded_plan_create/cancel/execution`、`saved_plan_execution_guard` 和 unresolved dispatch/recovery fence (`:990-1004`) 是 Host 安全边界；保留。
- M2 shadow `:1176-1224` 在 Turn 提交后执行，不能影响已提交执行；不要把它当成第二主链。

## 4. 本步识别的风险与待验证问题（不称为已复现安全漏洞）

| ID | 类型 | 证据 | 下一步必须验证 |
|---|---|---|---|
| F01 | **架构未完成** | `agent/config.py`: defaults `legacy` / `direct` / M5 disabled | 设计默认切换及显式回滚，确认 fake/dev/test 兼容；不能直接只改 3 个默认值 |
| F02 | **职责边界待收敛** | `main.py:544-723`: M5 `other` 后进入 generic Understanding/policy | 定义主语义 vs 目标理解/工具参数的所有权，不再由第二个“主意图”改写 M5 决策 |
| F03 | **Legacy 多入口保留** | `main.py:369,429-506,755-779` | Legacy-only 化，去除新默认的竞争入口；必要的工具/执行复核保留 |
| F04 | **异常输出要加测** | `main.py:313-360,1035-1077` | `SavedPlanRouteRefused` 安全拒绝；其他 `UnderstandingError`/invalid schema 进入 503，确认没有不安全的 fallback；给出错误码契约 |
| F05 | **无计划缺失引用** | `main.py:307-320,777-785` | “执行之前保存的第一步”在没有 plan 时不能触发任意 calculate，不能借 legacy classifier 产生执行权限 |
| F06 | **默认运行与验收不一致** | 当前默认 direct+legacy，而 M5 11/11 实测为 guarded+structured+enabled | 验证新默认 11/11、普通/待澄清/冲突及恢复，附精确 commit/config |
| F07 | **多模型调用不可误判为重复主路由** | 主语义与 action authorization、scope、Understanding、policy 分别存在调用 | 建立模型调用类别断言：primary owner =1，权限复核可以 >0；避免用总 HTTP 数作架构结论 |
| F08 | **恢复与高并发安全** | `main.py:244-270,990-1004,1081-1155` | SQLite + PG 的 claim/dispatch, stale snapshot, concurrent conflicting Turns, restart+unknown outcome，保证安全不因默认切换受影响 |

## 5. Step 2 设计必须交付的决策

1. 给出原路由节点与目标路由节点的 **before/after branch-condition map**，明确 pending/goal-confirmation 为独立 Host continuation，不与新主判定竞争。
2. 给出分类器/复核器逐个 disposition：**primary / scoped extraction / independent veto / Host authorization / legacy-only / remove**。
3. 规定哪些运行环境采用新默认（真实模型 + guarded），如何显式启动兼容模式；注入模型的测试、fake provider 的默认语义与配置迁移需单独验证。
4. 决定 `other` 的契约——可否继续普通 AgentLoop、何时必须澄清、是否禁止新计划写入，如何防止“无计划继续执行”绕过。
5. 完成 F04/F05 的失败场景设计：模型 schema 错误、原文 quote 不存在/不唯一、错误 revision/缺失 reference、冒用 pending、主路由已拒绝的副作用。
6. 标注不会修改的 Host 安全路径，明确 CAS/recovery/claim/dispatch/Turn finalization 不减弱。
7. 说明配置更改与 CI 测试迁移策略，明确完整验收最小集合和停止条件。

## 6. Step 1 出口 / 当前状态

**Step 1 = COMPLETE (static path audit)**。已找到明确的「默认未统一」「Legacy 入口存在」与「generic Understanding 职责需界定」事实；**没有**在这次静态审计中证明 M5 11/11 以外的复杂场景可靠，也没有证明某条路径存在安全绕过。

**下一步 = Step 2 目标架构与整改设计评审**。在获设计确认前，不改 `agent/config.py` 默认值、不删旧分支、不为 CI PASS 直接放宽 guarded Host Admission。只有一个最终集成 PR；不得擅自合并。
