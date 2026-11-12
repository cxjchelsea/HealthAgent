# S25-06 Step 2 — 多步骤规划与执行需求及行为契约（设计草案）

- 日期：2026-11-10
- 基线：main@9e2e79a473089be47d25d1c10b7a9fa82f06d89b
- 前置：S25-06-STEP1-BASELINE-GAP-AUDIT-20261110.md
- 状态：**DESIGN PROPOSAL — PENDING INDEPENDENT REVIEW / NO PRODUCTION CHANGES**
- 目标：最小可验证的有依赖串行多步骤计划；不提前实现 S25-07 通用重规划、S25-08 终局目标满足证明或并行 Worker。

## 1. 用户可见行为（必须满足）

B01. 用户要求「制定计划，不执行」时，生成有序、受证据绑定的执行计划，并展示依赖和待确认的动态输入；**创建计划不执行工具**。
B02. 用户明确批准首步时，仅执行一个 eligible 且获授权的步骤；不以准备依赖为由连续调用第二个工具。
B03. 用户批准继续执行剩余步骤时，按拓扑依赖选择就绪步骤，先完成依赖、再执行下游；在第一阶段采用串行拓扑顺序。
B04. 下游工具参数依赖上游结果时，只有前序 Observation 已通过 durable Receipt + Checkpoint、来源/版本/类型/大小校验后才解析参数。缺少数据、字段错误、类型不兼容须阻塞，不允许生成假值。
B05. 失败、中断、拒绝、超预算或用户取消不得被报告为完成；可独立完成的步骤是否继续，必须以本轮用户批准的范围、Host policy 和错误合同共同决定，默认失败即停止。
B06. 再次执行/恢复应保留已验证完成的步骤，未知派发先走 M4 reconciliation，严禁因恢复把副作用工具无证重放。
B07. 老版本已保存计划沿用当前线性语义；不在读取时自动升级为新依赖语义，也不将历史 NULL 工具版本当成最新版本。
B08. 系统不能将工具本身的 ok=true 自动归因为整个用户目标完成；最终说明应指出已完成、未完成、阻塞及证据范围。

## 2. 拟议数据契约：概念模型，不是最终代码接口

### 2.1 版本策略

新依赖计划需要显式 `plan_schema_version`，建议独立于工具执行器版本（`tool_version`）。以下仅为 Step 3 候选结构，不能直接覆盖旧 Checkpoint：
- `plan_schema_version = "dependency-plan-v1"`：表示新计划图的语义。
- `legacy-plan-v1`：现有工具适配器执行语义，**不是**新计划格式版本；对旧计划由显式 legacy adapter 维持线性处理。
- `tool_version`：每个可执行步骤固定受信执行器版本；历史 NULL 保留 unknown，并继续按现有兼容门禁处理。

**禁止**隐式迁移、按存在字段猜版本、将 ordinary Registry `1.0.0` 赋予保存计划中的旧工具。

### 2.2 Step 模型候选

| 字段 | 意义 | 约束 |
| --- | --- | --- |
| `id` | 不可复用的稳定 step ID | 正整数；同一 plan 唯一 |
| `goal` | 步骤目标 | 有界文本；不可充当执行许可 |
| `depends_on` | 依赖的其他 step ID | 去重、无自环、均存在；拒绝闭环 |
| `tool_name` / `tool_version` | 受信工具及版本 | Host 授权名单 + 保存计划执行器支持矩阵 |
| `argument_bindings` | 常量或前序 Observation 引用 | schema 验证；仅允许上游已完成的可信字段 |
| `status` | 正规步骤状态 | 运行态与持久事实应区分，见 §3 |
| `evidence_ref` | 已完成步骤的 Attempt / Dispatch / Receipt 关联 | 完成不可仅凭模型文本声称 |

`argument_bindings` 应采用**结构化 discriminated union**：
- literal：具备当前请求源证据的显式常量；
- observation_ref：引用 `source_step_id` + 允许的输出字段 + 类型/边界检查；
- 禁止模板字符串自由插值、任意 JSONPath、跨用户/跨任务引用、模型自造 Observation。
- 本期动态引用至少覆盖一种可控且能物理验证的受信只读工具数据类型；不默认开放任意工具输出。

### 2.3 依赖的精确定义

- DAG 边 `A -> B` 表示 B 只能在 A **已可信完成并持久化**后就绪；仅有 `observed` 而无 checkpointed 的 Receipt 不足以允许继续。
- 若 B 引用 A 的 Observation，则 A 必须存在于 B 的 `depends_on`；A 的证据字段必须在固定 allowlist 内。
- 无依赖步骤的 ready 集合按原始 plan 的稳定序号/step ID 选择，第一版逐项串行执行，拒绝隐含并行。
- 拒绝未知 step ID、重复依赖、自依赖、循环依赖、前向未完成依赖、跨 Plan Revision 悬空引用。
- 可复用已完成步骤的证据必须携带相容 revision 与版本；计划修订时失效数据不得继续作为下游参数。

## 3. 状态机（先定义语义，再决定持久化实现）

独立于旧 checkpoint 的 pending/completed，候选外部呈现状态：
- pending：尚未满足依赖、尚未取得执行资格；
- ready：依赖已满足且 Host 可重新审核，**不是执行授权**；
- running：执行前已获得本轮 Claim/Dispatch 所需资格；
- completed：已验证成功并持久化 checkpoint；
- failed：确定性执行失败，保留不可伪造的证据与错误；
- blocked：缺少依赖证据/参数/授权，不执行；
- interrupted：执行状态不确定或崩溃，交 M4 恢复核实；
- superseded：修订后旧步骤不再属于有效计划。

**设计限制：** 不要求一次性把上述所有值写入旧 `PlanLedger.restore`；Step 3 必须给出版本化状态映射、TaskState projection 改动最小化和兼容迁移方案。失败不可简单通过改成 pending 隐藏；也不能用失败状态导致旧快照无法被恢复。

## 4. 规划提案、校验与调度的职责划分

1. **Primary / Planning proposal**：在模型上下文中提出 2–8 步的合理分解、目标、依赖、源引用及可绑定工具参数；仅产生受信前待验证数据，不授予执行权。
2. **Host validator**：确定性检查用户目标来源、DAG、工具许可、参数 Schema、绑定来源、版本和预算；缺失输入则澄清或拒绝，绝不自动补成固定参数。
3. **Persistent Plan Store**：以原子 CAS 持久化经过检查的计划；创建计划不得 dispatch。
4. **Ready selector**：从最新可信快照计算 ready set，不直接写入授权信息；保持串行稳定次序。
5. **Guarded M4 Executor**：每个步骤执行前重新检查本轮执行范围、Turn、Claim、Checkpoint 和 Host 权限，再进入 `PlanReadInvoker`/Dispatch Journal/Receipt/Checkpoint CAS。
6. **Result resolver**：从已经 checkpointed 的受信 Observation 提取允许字段、完成 Pydantic 类型检查，解析下一步参数；模型输出文本不是实际 Observation。
7. **Evidence projection**：TaskState/Attempt/Dispatch 建立步骤到结果关联；失败保留原因，回答不得虚构结果。

## 5. 授权范围合同

| 请求 | 允许 | 禁止 |
| --- | --- | --- |
| plan_create | 验证并保存结构化计划 | 执行任何工具 |
| plan_execute_first | 执行**最多一个**本轮选中的就绪步骤；若第一步不可执行则澄清/阻塞 | 自动补做前置依赖、多调用工具、修改计划 |
| plan_resume | 在明确授权的保存计划范围内按拓扑串行执行符合条件的剩余步骤 | 执行新增步骤、升级工具授权、跳过失败依赖 |
| plan_revise | 确认新 Revision，审查继承/失效的证据 | 未批准情况下执行改写后的步骤 |
| plan_cancel/withdraw | 按既有 Pending、Claim 和中断合同拒绝新的工具派发 | 声称已撤销不可逆外部副作用 |

目前 `plan_execute_first` 原语义是已保存计划的“第一待执行项”；设计审查必须决定并测试 DAG 条件下是否坚持**原始顺序第一步**或改为**首个 ready step**。建议首次版本要求保存计划本身拓扑有序，拒绝首项缺少前置证据的情形，不静默更改用户可见的“第一步”含义。

## 6. 失败处理及 S25-07/S25-08 边界

- 06：依赖引起的阻塞、参数绑定失败、失败后停止、可信 Checkpoint 恢复、部分执行结果报告。
- 07：Observation 失败后**模型自主提出**替代步骤/替代工具/局部重新规划、失效关系传播和重规划质量。
- 08：根据用户整体目标判定最终充分性、证据汇总、部分完成与交付物完整性。
- 06 不加入新的关键词路由、宽泛自我修复、无限重试或 M4 之外的恢复派发。
- 06 第一版不做计划步骤并发；必要时在评测中量化瓶颈后独立论证。

## 7. Step 3 设计待决事项

D1. 是否复用 `PlanLedger` 并增加 versioned dependency adapter，或单独的新版 schema projection，但只有**一条** Host/M4 执行路径？
D2. 受信 Observation 的最小输出字段（例如 MCP 的 `definition/source`、calculate 的 `value`）及字符串/数字绑定类型。
D3. 旧版 snapshot 的迁移/双读规则与版本升级策略：NULL 不是新版本。
D4. 在哪个数据库事务边界提交 step completed 与 Observation 关系，确保下游读取时 receipt 已 checkpointed。
D5. 有关“first”语义、blocked step 与 independent steps 在同一次 resume 中的行为和预算上限。
D6. 新增状态与 S25-03 TaskState Projection、history、owner isolation 的兼容策略。
D7. 计划创建时模型提出依赖关系的可复现多轮真实模型评测门槛。

## 8. 验收协议（Step 2 合同，不代表实现已通过）

- 模型语义：2 类以上未知依赖任务 × 至少 2 次真实模型，记录路由/计划有效率；禁止按固定中文句式硬编码。
- DAG：完整验证环、未知依赖、非法引用、同一步重复边、自依赖与越界列表；所有非法计划零 Dispatch。
- Observation：上游未 checkpoint、错误来源/字段/类型、跨修订/跨用户均不得绑定；受信正常引用结果与物理 Receipt 一致。
- Host：plan_create 零派发，execute_first 最多一个，resume 不越权；所有拒绝带稳定原因码。
- M4：PostgreSQL 并发 Claim、crash/unknown Dispatch、Receipt/Checkpoint CAS、重复恢复；证据不得伪完成。
- Legacy：已有 `legacy-plan-v1`、historical NULL 的实际恢复测试；新格式不被旧执行器静默消费。
- 报告：需区分 static tests、HTTP/SQLite、PostgreSQL 物理、ollama_proxy/qwen3:14b 真实模型，CI 成功不冒充真实模型。
- 不增设默认开启的危险工具，且在用户正式批准 Step 4 前不得修改生产路径。

## 9. 本轮状态及下一步

当前文件是 Step 2 行为合同草案；只通过代码静态审查推导，**尚未通过单独的契约复审**。Step 1 的本地物理测试尚未提供，PR 的 CI 状态需要单独核验。待 Step 2 审查明确 D1–D7 后，再起草 Step 3 可实现的接口与迁移设计；禁止以本设计草案的存在宣称 S25-06 已实现。
