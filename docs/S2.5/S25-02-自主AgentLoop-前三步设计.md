# S25-02 — 自主 Agent Loop：前三步设计

> 版本：Design V1.0，供用户审查；**未获 Step 4～6 实施授权**  
> 上位规范：`docs/S2.5-通用自主Agent能力补齐开发策略.md` V2.0  
> 当前审计基线：`main @ 01520ed2c5443f5c5ae91d477ee7b44a8626f9d1`（2026-10-21）  
> 能力状态：**DESIGNING / DESIGN REVIEW REQUIRED**  
> 实施范围：严格为六步法 **Step 1 能力目标定义、Step 2 现状及 Gap 审查、Step 3 独立详细设计**。没有改运行代码、迁移、Prompt 或测试；Step 4 实现、Step 5 验收、Step 6 收口均需要用户另行批准。

## Step 1 — 目标能力与用户可见行为

### 1.1 为什么单独建设 S25-02

S2 的 Agent 已能对真实 Qwen 进行 Tool Calling、反复决策、处理工具 Observation、持久化基础 Plan、返回 partial；但通用自主行为尚未被统一定义：模型何时直接答复、调用工具、提出计划、澄清、暂停、拒绝、继续、终止以及错误恢复，仍主要隐含在 `AgentLoop._decide/_tools` 与少量话术特判中。部分测试通过不等于陌生任务可自主完成。

**能力目标**：保持一个正式 Conversation API 和一个 AgentLoop，采用模型驱动的动作选择与确定性控制相结合，推动目标直到可验证的完成、部分完成、等待用户、拒绝或失败；每次继续/停止有明示原因，不凭工具 `ok=true` 推断总体目标完成。

### 1.2 职责边界

- **S25-01 Understanding** 提供经校验的目标/约束/澄清需求；尚处设计审查阶段，S25-02 先依赖其最小语义接口，不并行开发第二个理解器。
- **S25-02 AgentLoop** 负责一个回合内决策与行动控制、循环边界、根据 Observation 选择继续、请求修订、澄清或终止，并输出终止原因；**不**直接拥有目标事实权威、持久化事务、审批签发。
- **S25-03** 负责 Goal/Plan/Step/Attempt/Observation/Artifact 的可信状态与证据（本阶段只约定最小投影）。
- **S25-04/05** 决定允许暴露哪些工具及能力元数据；Loop 只能调用经授权的工具执行器。
- **S25-06/07/08** 分别深化规划、重规划及完成验证；S25-02 不提前再写一套 Planning Ledger。
- **S25-09/10/11/12/13/14/15/16/17/18** 分别深化上下文、记忆、恢复、审批、安全、错误、Harness、交互、Trace 与评测。S25-02 只做对主链闭环所必需的最小衔接。
- **不做**：领域知识/RAG 实现、临床逻辑、多 Agent、分布式 Scheduler、第二套 Agent Runtime、以自然语言关键词脚本决定动作。

### 1.3 可观察行为矩阵

| 场景 | 正常期望 | 失败/反例要求 |
|---|---|---|
| 简单问题 | 无需工具时直接答复，低延迟 | 不强制建计划或工具调用 |
| 可执行工具目标 | 模型判断需要何种工具；执行后读取真实 Observation 再决定是否继续 | 工具失败不假装成功 |
| 无具体检索对象的“查 MCP 资料” | 使用 Understanding/工具输入缺口作澄清或有依据的能力说明 | 不擅自把“查询到了什么”当 term |
| 复杂多步骤 | 需要时提出计划，迭代执行，检验完成与剩余项 | 模型不得无验证结束并声称完成 |
| 失败后策略修正 | 读取失败原因，选择合理重试、替代、重规划、澄清或终止 | 不无限重试同一无效动作 |
| 目标更正 | 消费已确认目标变化、保留无关证据、废止被替换证据 | 不依赖“不是8*8，而是9*9”正则作为通用语义理解 |
| 权限不足/写副作用 | 生成受限行动建议，等待授权或拒绝执行 | 用户话语、模型 Tool Call 不等于权限 |
| 任务暂停或取消 | 产生可观测且准确的状态和后续动作 | 用户点停止等待不谎称服务端取消；不能在取消后继续发起新副作用 |
| 预算耗尽/提供商不可用 | 明确状态、保留可恢复信息、给出可操作建议 | 不能返回 `completed`；不能无限循环 |
| 恢复后继续 | 从可信执行状态恢复，并校验 Observation 与权限有效性 | 不把未知执行结果当成功 |

### 1.4 质量与验收预设

- **正确性优先**：真实工具返回失败、结果不完整或缺少证明时，不得宣布 `completed`。
- **有界**：单回合模型决策次数、工具次数/扇出、Token 和耗时均有限额且可记录；复杂任务可在跨回合继续，不靠单回合无限循环。
- **延迟控制**：简单对话不应每轮无条件多次调用 Understanding+Planning+决策模型；承接 S25-01 fast path 与模型分工设计，真实 P50/P95 后再定阈值。
- **安全**：模型选择不能绕过工具白名单、owner 隔离、记忆写入同意和将来的审批边界。
- **兼容**：既有 `POST /conversations/{cid}/agent/messages`、S2 的 SQLite/PostgreSQL、保存的 Checkpoint、网页与原生 Tool Calling 保持兼容。
- **证据**：确定性单测、正式 API/DB 集成及真实 Qwen 未见过的任务分别证明；强制记录失败样本、决策次数、时延、状态、工具调用与依据。
- 目标验收不以一条算术案例全绿为充分条件；场景和统计阈值应在 Step 4 前冻结。

## Step 2 — 当期 main 代码路径与 Gap 审查

### 2.1 现有正式路径（代码核验）

`apps/api/main.py` 正式会话端点 → owner 与并发 Turn 校验 → `agent/context.py` 历史选择 → `ConversationPlanStore.load()` → `AgentLoop.run(goal, history, plan_checkpoint)` → LangGraph `decide ↔ tools` → `NativeToolDecision`（Ollama/OpenAI-compatible，`think=False`、非流式、空决策至多重试一次） → 白名单 Tool / MCP / Memory / PlanLedger → Observation → 模型再决策 → API 同事务提交 assistant + Turn + checkpoint（仅成功/partial 时）。

实查相关文件：`agent/loop.py`、`agent/decision.py`、`agent/skills.py`、`agent/tools.py`、`agent/planning.py`、`agent/understanding.py`、`agent/context.py`、`agent/config.py`、`services/plan_store.py`、`tests/test_s2d_goal_correction_guard.py`。现有 `agent/understanding.py` 是旧 S1 规则 intake，不是已接入主链的模型语义理解系统。

### 2.2 复用、改造、新增的证据表

| 子能力 | 当前结论 | 代码/实证 | 设计动作 |
|---|---|---|---|
| 唯一 AgentLoop 入口 | 已实现 | `AgentLoop`/正式会话 API | **复用**，禁止第二入口 |
| 真实模型原生工具选择 | 已实现 | `NativeToolDecision`、Qwen S2 实测 | **复用**，新增明确的 Loop 决策语义映射 |
| 基本行动与 Observation | 已实现 | `_tools`、`execute_tool` | **保留执行器**，改进终态判定 |
| 有界决策、工具白名单 | 部分实现 | `max_steps`、单轮最多 4 工具；`exposed_tools` | 保留限制，增加统一预算与原因码；工具预算归 S25-15 |
| Clarify / Need Input | 部分/缺口 | 模型可在正文追问，但无可信澄清等待状态 | 设计 `needs_input` 终态及跨回合恢复；细节依赖 S25-01/16 |
| Pause / Cancel / Denied | 缺口 | `decide/tools` 图缺显式路径；网页“停止等待”只是客户端行为 | 新控制信号、执行前阻断，用户授权与取消语义留 S25-12/16 |
| Budget Exhausted/Failed | 部分实现 | `step_limit/tool_limit/provider_error` | 统一公开终止原因、区分内部失败与等待 |
| 目标更正通用化 | 部分/缺口 | 主链有算术正则和显式记忆文本触发 | S25-01 提供变更提议；逐步撤出主链语义特判 |
| 重规划触发 | 部分实现 | `revise_plan` 与工具错误提示已接通 | 模型策略选择保留，执行层只验有效状态、权限与证据 |
| Completion 可信验证 | 部分实现 | `PlanLedger` pending 和 superseded 检查；无计划时易依模型 final | 定义必要的完成证据/未完成原因；S25-08 再深化 |
| 长任务/恢复 | 部分实现 | 基础 checkpoint/人工 interrupted 清理 | 最小恢复入口，未知副作用不自动重复，深化留 S25-11 |
| Context/Memory 状态可信 | 部分实现 | Context 只保留 completed 历史；部分状态独立保存 | 定义跨回合最小目标/暂停视图，详细治理 S25-09/10 |
| Trace / Benchmark | 部分实现 | 有 `traces`/audit 和 S2 benchmark，但并非完整因果链 | 最小决策与终止字段，深入留 S25-17/18 |

### 2.3 关键风险与设计决策

1. **不要把所有路径写成八九个 LangGraph 节点**：逻辑行为完整不等于新增同数目的物理节点。优先演进现有 `decide/tools`，增加小型路由及终态函数即可。
2. **S25-01 尚未实现**：不能假定其 GoalSpec/ChangeProposal 已可用，预留兼容入口，Step 4 以用户审核后的契约绑定；无可靠目标解释时谨慎答复/澄清，不使用正则替代通用语义。
3. **完成证据不充分**：目前有模型 final 即可能 completed；设计守卫必须区分回答型目标和需要工具/副作用的目标，防止所有自由对话被强行判 partial。
4. **动作与副作用权限**：模型 Tool Call、用户句子和有效审批不能混为一谈；本能力只传递控制需求，实际权限约束在可信执行层。
5. **跨回合暂停与取消**：不能仅返回 UI 文本而丢失等待原因；暂停后服务端恢复/取消要明确谁具有权限，且无法杀死已经发生的外部写副作用。
6. **性能/模型预算**：现有 Qwen3:14b 单条工具调用很快，但多节点和结构化理解可能放大延迟；只采集必要数字，不提前承诺性能指标。
7. **兼容旧状态**：保留旧 `completed/partial/provider_error/step_limit/tool_limit` API 语义，新增细分 `termination_reason` 而非直接打破前端。

## Step 3 — 独立详细技术设计

### 3.1 唯一主链与候选方案

逻辑流程：

```text
正式 Conversation API
    ↓ 校验 owner / 接纳 Turn / Context / 已有 Checkpoint
Understand (消费 S25-01 目标解释；可延后接入)
    ↓
Decide (LLM: Answer / Tool / Plan / Replan / Clarify / Wait / Stop)
    ├─ Clarify → NeedsInput (持久原因/问题)
    ├─ WaitApproval → Paused (不得执行未授权副作用)
    ├─ Refuse → Denied
    ├─ Answer → ValidateFinal → Completed / Partial
    └─ ProposeAction → TrustedGate → Act → Observe
                               ↓
                       Decide 继续 / Replan / Clarify / Finish
异常或控制信号 → Failed / BudgetExhausted / Cancelled / Interrupted
```

**选择 A（推荐）**：在现有 `AgentLoop` 内，保持 `NativeToolDecision` 返回正文/工具请求的数据兼容；用轻量 `LoopControl` 分类决策和有限的终态处理逻辑，实现明确状态机与记录，必要时扩展小型路由。S25-01 的澄清结果由主链消费，不要求模型每轮使用独立“控制工具”。优点：少改、兼容原生 Tool Calling；缺点：有些控制语义需额外结构化判定。

**备选 B**：强制模型每回合输出 JSON union `Answer|Act|Clarify|Replan|Pause|Finish`，所有工具调用通过包装工具。优点：动作类型统一；缺点：会破坏现有原生工具协议、Proxy 兼容性可能下降，还会增加模型及适配成本。**暂不采用**。

**备选 C**：单独构建 Workflow Engine/Runtime。与本阶段单主链、功能优先原则冲突，**不采用**。

### 3.2 决策/状态最小契约（拟议，不等同已实现）

```python
LoopControl:
    turn_id: str
    goal_version: int | None
    decision_index: int
    model_budget_remaining: int
    tool_budget_remaining: int
    cancellation_requested: bool
    awaiting: "none | clarification | approval"

ActionProposal:
    action_id: str
    tool_name: str
    arguments: dict
    impact: "read | write | external"
    # 此处不含 approval=True 之类不可信字段

Observation:
    action_id: str
    tool_name: str
    ok: bool
    error_code: str | None
    retryable: bool | None
    result_ref: str | None

LoopOutcome:
    status: "completed | partial | needs_input | paused | denied | failed | budget_exhausted | cancelled | interrupted"
    termination_reason: str
    answer: str
    checkpoint: dict | None
    trace_id: str
    remaining_goals: list[str]   # 最小展示投影，不重复拥有 S25-03 权威状态
```

契约遵循“最小边界，不提前冻结 S25-03～S25-18 对象”。对象为 **设计草案**：审批 Authority、GoalSpec 结构、持久存储模型和工具分类枚举应分别经对应能力审查后最终确定。

**兼容建议**：API 的 `status` 可以先映射回原 `completed/partial/provider_error/step_limit/tool_limit`，新增只读的 `termination_reason` 和 `needs_input` 投影；前端/数据库完成迁移且通过测试后才启用更细状态。未经批准不得修改 schema。

### 3.3 决策规则与状态转换

| 来源 | 事件/验证 | 下一动作或终态 | 约束 |
|---|---|---|---|
| 新请求 | 目标解释已确认/足够 | Decide | 不能假设待办引用存在 |
| 新请求/Observation | 核心参数缺失且执行会误操作 | NeedsInput | 只问最少关键问题，保存待澄清目标 |
| Decide | 没有动作，证据足够 | Completed | 区分无需工具的对话型目标 |
| Decide | 没有动作，但目标证据不足 | Partial / NeedsInput | 不因模型说“完成了”而结束为 completed |
| Decide | 一个或多个工具建议 | Gate → Act | 每个动作独立校验白名单、schema、用户、权限、预算 |
| Act | 成功 Observation | Decide/Verify | 工具成功只是动作结果，非目标成功 |
| Act | 可重试错误 | 模型决定合理再试/换方法 | 有界尝试，别重复无效参数 |
| Act | 不可重试错误/拒绝 | Denied / Partial / Failed | 不自动升级权限或伪造结果 |
| Decide | 请求更改计划 | 复用 PlanLedger 修订 | 确认版本、废止与保留证据 |
| 外部 | 有效取消且动作未提交 | Cancelled | 禁止后续动作；未知外部副作用需留待 S25-11 |
| 外部 | 授权未满足 | Paused | 动作建议需持久化待审批；本能力不签发授权 |
| 任何点 | 决策/工具预算耗尽 | BudgetExhausted | 输出部分成果及未完成项 |
| 任何点 | Provider/工具协议故障 | Failed 或可恢复 Partial | 记录原因，不掩盖为 Completed |
| 读取旧状态 | 恢复状态不确定 | Interrupted/Paused | 不重复执行未知外部效果 |

**优先级约束**：可信执行拒绝 > 取消/审批约束 > 输入澄清 > 动作执行 > 模型 final；模型不得覆盖 gate 已拒绝的事实。不可取消已提交外部副作用。

### 3.4 回合生命周期、幂等与事务边界

- **进入**：正式 API 验证身份、会话 Owner、重复 request id、并发 running Turn；读取 Snapshot、有限上下文和理解结果。
- **运行**：每次模型决策有编号与版本，工具候选通过 allowlist、schema 和必要授权检查；工具结果形成关联的 Observation。无效调用失败后可反馈模型，但受预算限制。
- **退出**：仅将满足证据标准的目标标 `completed`；否则选择 partial / needs_input / paused / denied / budget_exhausted 等，输出下一步所需信息。已有 Assistant/Turn/Checkpoint 在本地 DB 同事务的保证保持不变。
- **中断**：拒绝把已完成的外部副作用当作未执行；旧人工 recovery 仍可保留作为保守兜底。真正 crash-safe 恢复、outbox/幂等深入 S25-11。
- **持久化**：最小需要 `goal_version` 或目标引用、`termination_reason`、待澄清/待审批原因以及状态绑定。**具体字段与是否迁移均留 Step 4 前独立兼容核对**；不另建第二份 Plan/GoalState 权威表。
- **多轮**：收到补充信息，先由 S25-01 生成有版本的继续/更正提议，再由执行主链使用同一任务状态继续；无可信指代不得猜测。

### 3.5 跨能力对象与旧模块退出策略

| 交界 | 输入/输出约定 | 主导能力 |
|---|---|---|
| S25-01 → 02 | `GoalSpec` / `ChangeProposal` / 是否需澄清 / 可信来源 / 版本；解释结果不是授权 | S25-01 |
| S25-02 ↔ 03 | 最小 TaskState/PlanState 只读视图、Observation 引用、状态变更请求；不写第二套权威存储 | S25-03 |
| S25-02 → 04/05/12/13 | ActionProposal → 可执行决议 → 工具 Observation；拒绝原因和权限依据是确定性的 | 工具治理/权限 |
| S25-02 ↔ 06/07/08 | Decide 可提议计划/修订；PlanLedger 守卫及完成证据由对应能力深化 | Planning/Validation |
| S25-02 ↔ 09/10/11 | 有预算上下文、授权记忆读取、恢复检查点；工具副作用未知需暂停确认 | Context/Memory/Recovery |
| S25-02 → 16/17/18 | 对用户可见的等待/取消状态；审计决策与工具证据；可复现评分结果 | HITL/Trace/Benchmark |

迁移路径：先把现有 `AgentLoop` 当唯一执行器，在原 `_decide/_tools` 内逐步接入最小控制契约及稳定终止码；S25-01 已审核实现可用后才替换 `run()` 中算术正则与“还记得”等话术特判。**不并存两套最终生效的理解器**；shadow 对照只能暂时诊断，不可另建对外决策入口。

### 3.6 风险、安全和错误可解释性

1. **Schema 正确不代表业务允许**：`model_tool_call` 必须经工具曝光策略及可信授权，写入、记忆与外部操作须显式权限。
2. **Tool/网页低信任文本**：不得提升为系统指令；不经可信 schema/来源验证不得参与状态权威决定。
3. **用户中途更正**：读取旧 Goal/Plan 版本，验证仍相关的 Observation，不删除无关已完成证据；版本过期时拒绝旧决策。
4. **进程和网络中断**：区分模型未响应、调用未开始、执行已发出但结果未知；后者不得自动重试写工具。
5. **Trace 隐私**：记录 Decision ID、Action ID、状态变化、时长与错误类型，用户文本/工具原始输出默认不进入普通日志。
6. **容错回退**：当真实 Provider 返回空决策或坏协议，不伪造模型回答或工具调用；区分重试是否安全。
7. **过拟合风险**：不针对“查 MCP 资料”或算术例句增加一条关键词主链规则；用变体及未见任务检验语义泛化。

### 3.7 预计修改范围（实施前审查，不是已改）

- **核心**：`agent/loop.py`（唯一 Loop）、`agent/decision.py`（只在确需控制信号时最小兼容扩展）、`agent/skills.py` / `agent/tools.py`（保留工具选择与确定性校验）。
- **接线**：`apps/api/main.py`、`agent/context.py`、S25-01 经批准的 `agent/understanding.py` 统一入口；如需新 DB 字段再评审迁移。
- **测试与评测**：`tests/test_agent_loop_*.py`、正式 API 相关测试、`evals/s25_02_live.py` 或对现有 S2 runner 小幅扩展；Trace/Benchmark 元数据原则上复用当前设施。
- **非修改目标**：不拆 API、不会重做计划数据库、不引入第二个 Provider/AgentRuntime、不重做 Memory/审批/Recovery 子系统。

### 3.8 Step 5 验收设计（此处只冻结标准，不执行）

**确定性与集成**：
- T01 简单回答不必调用工具，正常结束且有 termination_reason。
- T02 模型请求工具，可信 gate 通过才执行并附 Observation；未知工具或参数错误返回 typed failure。
- T03 已执行工具失败，模型有界重试/替代；无有效结果不得 completed。
- T04 缺少明确检索对象或多义指代 → needs_input；不同语言/同义变体通过同一语义路径（依赖 S25-01）。
- T05 用户新增任务与替换目标产生不同有效状态；已验证步骤保留，废止只针对受影响部分。
- T06 未获授权的持久写操作 → paused/denied，外部执行次数为零。
- T07 取消、审批待决、预算耗尽、Provider 失败有明确终止码，暂停恢复无重复写副作用。
- T08 正式会话 API 多轮 + SQLite/PostgreSQL + Checkpoint 恢复 + owner 隔离 + 并发 409 回归。
- T09 Prompt Injection、伪造 Observation、坏 Tool Call、错误参数、重复 action、迟到结果对抗。
- T10 API UI 可显示 partial/needs_input/paused 等合理状态，刷新不误判完成。

**真实模型 E2E**：配置明确 Qwen 模型名称/端点/temperature/输出预算，覆盖简单对话、精确与模糊 MCP、算术、复合工具、目标新增/更正、可重试与不可重试错误、澄清、暂停/授权、跨轮继续；场景包括不在 Prompt 中的变体。记录单次成功率、至少重复运行后的波动、P50/P95、模型决策次数、工具成功率、错误类型和真实用户可见终态。禁止 Scripted/Fake 测试标为真实 Agent 智能。

**验收边界**：S25-02 无需等待 S25-03/11/12 的全部高级功能建成；但只要承诺的暂停/失败/取消等状态不能正确接线，就不得标记该项 CLOSED。S25-01 尚未通过时，依赖理解能力的集成项标注 BLOCKED_BY_S25-01，不虚报 S25-02 完成。

## 设计审查待确认

1. 是否认可在现有 `AgentLoop` 里引入最小控制结果与终态码，而不是新增第二个 Agent Runtime/独立规划引擎？
2. 是否接受先复用原生 Tool Calling + 轻量控制映射，避免要求模型每轮输出强制 JSON union？
3. 是否接受 Clarify/Pause/Cancel 使用最小明确状态与确定性权限校验，具体审批及长期恢复细节留 S25-12/16/11？
4. 是否接受“工具成功不代表目标完成”，但简单聊天可在无工具证据下合理完成的分层 Final Validation？
5. 是否接受 S25-01 未通过时只定义接口、不得提前把算术正则替换为另一套关键词语义路由？
6. 是否批准下一轮在本文件设计经审查通过后，再启动 **Step 4 实现**？（本次不构成授权）

**本轮结论：Step 1/2/3 DESIGN REVIEW REQUIRED；Step 4/5/6 NOT STARTED。**

---

## Step 4 — 第一轮渐进实现日志（2026-10-21）

> 此节为用户批准设计后开始的真实实施记录。Step 1/2/3 的设计基线不被追认成运行证据。本轮只实现**统一退出原因最小切片**，不是整个 S25-02 已实现。

- 在原 `agent/loop.py` 增加 `termination_reason`，沿用唯一 AgentLoop、LangGraph `decide ↔ tools`、原生 Tool Calling 和正式 API；不增加独立入口、新 Runtime 或 DB 表。
- 区分直接回复 `model_final`、仍有计划步骤 `pending_plan_steps`、旧完成证据冲突 `superseded_evidence`、记忆不可验证 `memory_recall_unverified`、模型决策预算耗尽 `decision_budget_exhausted`、工具扇出过限 `tool_fanout_exceeded`、第一步有界执行 `bounded_partial_execution`、Provider 精确失败码等原因。
- 现有 API 响应透传 `AgentLoop.run()` 字段；仍保留旧 `status` 和 API 对非完成状态返回 503 的行为，**未声称完成需要 API 状态迁移的澄清/暂停/拒绝/取消功能**。
- 新增 `tests/test_s25_02_loop_termination.py`，覆盖直接回答、pending checkpoint、过量工具、模型步数和 Provider 空决策；这些使用受控决策器而非真实模型。
- 下一切片优先评估可信澄清/暂停动作与 API 生命周期兼容；必须先与 S25-01 的 Understanding 结果对齐，不在本阶段临时增加关键词识别。
- Step 5 真实 Qwen / 正式 API E2E、Step 6 完成判定**尚未开始**。CI 要绑定最新精确 HEAD 单独核验，不把新测试文件存在当作已通过。

**实施状态：IMPLEMENTING（Step 4 started, incremental slice 1）。**

### Step 4 — 第二切片：同回合确定性失败动作防重复（2026-10-22）

在现有 `AgentLoop._tools` 内登记单回合发生的不可重试工具错误（`invalid_arguments`、`reference_not_found`、`tool_not_exposed`、`mcp_tool_not_allowlisted`、`unknown_tool`）对应的工具名及精确参数。模型重复提交相同普通工具调用时不再次执行，返回 `repeated_nonretryable_action` 工具 Observation，允许模型修改参数或选择替代工具。成功工具调用不受该规则影响；每次新回合重新初始化记录。规划修订工具保持现有 Version/Invalidation 验证逻辑，不纳入此防重复规则，以免拦截合法计划状态变更后的修订。新增 `tests/test_s25_02_repeat_failure.py` 包含失败动作重复、变更参数成功、重复成功只读工具调用等受控决策测试。

此切片不增加用户意图识别、审批、取消、长事务或跨回合幂等保证；不是对所有失败类型无条件禁止重试。自动化 CI 和真实 Qwen E2E 证据必须分别核验，S25-02 继续 **IMPLEMENTING**。
