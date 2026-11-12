# S25-07 Step 2 — Observation 驱动重规划：行为合同与验收标准（2026-11-11）

> **状态：DRAFT — awaiting user review / Step 3 independent technical design.**
> **仓库基线：** main@`e02845f7c3452ef452efceb20f7b725e5716529b`；基于 `S25-07-STEP1-MAIN-GAP-AUDIT-20261111.md`。
> **交付物：** 本文只冻结建议行为、能力范围、权限/失败边界和验收 oracle；不修改 runtime、DB、API、开关，不宣称测试通过。
> **关联：** S2.5 V2 策略；S25-06 Step 6 最终审计与 integration handoff。

## 1. 定位与可观察的交付目标

**目标：** 在单一正式端点 `POST /conversations/{cid}/agent/messages` 中，基于已有 Host 验证的真实工具 Observation / 可信失败证据，允许 Primary 提出继续、受限重试、同权限范围内的可行替代、局部修订、澄清、停止；Host 决定准入，原子保存修订版计划及引用，保留有效已完成步骤，在有执行授权且预算允许时继续执行。用户能够看到真实状态（未完成、已保存修订、已继续或需要人工操作），不得虚报已完成。

### 1.1 与现有系统的严格边界
- S25-06 的 `dependency-plan-v1`、`dependency-plan-read-v1`、顺序执行、2–8 步、已验证 `observation_ref` 是现有实现基线，不得当作已经可修订。Primary Host 当前明确对新格式 `plan_revise` 返回 `dependency_control_not_supported`。
- 普通 AgentLoop / legacy PlanLedger 的已有反馈与修订仅算可复用经验，不是新格式重规划的成功证据。
- Step 2 只定义可交付用户行为，不在此决定持久化字段、revision 的实现手段、具体接口签名；这些是 Step 3 工作。
- **“自主”不是无限循环：** 自动推进仅能发生在原 Turn 可证明覆盖的只读范围内，有单次决策/动作预算且所有状态/证据/权限检查通过；否则安全结束当前 Turn，下一轮需重新 Primary + Host 准入。不能用工具错误文本制造新的用户授权。
- 初始新能力 opt-in，原 `s25_06_dependency_plan_enabled=False` 默认不变；新开关及启用组合在 Step 3 设计。
- 新计划修订不得静默迁移到 legacy 格式，不重新引入独立关键词路由。

## 2. 输入事实、决策和输出合同

### 2.1 可信事件类型（候选行为合同；数据结构待 Step 3）
| Host 事实 | 判据 | Primary 合法建议 | Host 必须做的事 |
| --- | --- | --- | --- |
| `verified_success` | 成功 Receipt 已匹配 dispatch/attempt/checkpoint 并满足步骤证据要求 | continue / revise remaining / clarify / stop | 校验依赖、预算及当前授权；不得把工具成功直接当目标完成 |
| `verified_failure` | 有可信 Receipt，明确 `ok=false`，且能核验属于当前 step/attempt | retry / revise remaining / clarify / stop | 先分类与校验重试条件，生成**新的** dispatch/attempt；不得覆盖原失败 |
| `invalid_arguments` | Host/Registry 类型或来源校验拒绝；可能根本没 dispatch | propose corrected binding / clarify / stop | 只认可来源与 schema 合法的参数，不得靠文本自动改出“授权” |
| `capability_unavailable` | 工具缺失、失效、版本不匹配或不可达 | replace if truly equivalent and authorized / clarify / stop | Registry、等价约束、版本和权限全部重审 |
| `permission_denied` | Host 拒绝本次执行 | clarify / stop | 不自动升级工具/范围/写入权限，不将模型提案视为 consent |
| `dispatch_unknown` | 有 pre-dispatch 但缺可信可证明最终结果，或 crash 后状态未核验 | stop/pause; request trusted recovery | **禁止重试、替换、下游执行或伪造失败**；沿用原 Fence/recovery |
| `stale_or_conflict` | plan/Turn/Claim/Fence/CAS 或源证据已经变化 | reread trusted state / stop | 原子拒绝旧提案与旧派发，要求新授权 |
| `goal_or_constraint_change` | 当前用户明确更改目标/约束，或 Primary 得到有证据支持的新目标 | propose revision / clarify | 重新绑定当前 Turn 与用户目标，不得用工具内容冒充用户命令 |

工具返回的 `error` 是**诊断数据而不是授权事实**。例如“工具超时”不直接证明没有副作用，也不直接准许重放。Host 将失败/未知分类为可执行前置条件；模型负责根据可信事实与任务语义提议修订理由和新计划。

### 2.2 最小动作集合
- `continue`：无需改计划；仅在依赖可用、此前未越过失败/未知及预算许可时继续。
- `retry`：仅针对已验证失败且可重试的**只读**动作，需新 dispatch/attempt，次数受 Host 管理；不能原样重放 unknown。
- `revise_pending`：根据 Observation 修订**未执行步骤/后缀**的合法工具参数或依赖；须原子生成新 revision、保留可信完成前缀与不可变来源 ID。
- `replace_tool`：仅在真实存在经验证能够满足同一步输出合同、相同或更小权限的工具时允许；缺等价工具时返回 `capability_unavailable`/澄清，不得制造“替代成功”。
- `clarify`：缺少可验证参数来源/用户语义判断、冲突或新范围需用户决定时不执行。
- `stop`：不可恢复/证据未知/预算耗尽/权限拒绝/修订不满足约束时保留部分成果、提供机器可判定原因。
- `cancel` 和完整主动暂停/撤销不在本步；归 S25-16。完整最终交付质量归 S25-08。

### 2.3 面向用户的最小反馈
每次重规划决策至少能通过当前正式 API 的稳定状态/trace 或既有响应字段展示：
- 是否继续、是否实际派发工具、是否保存了修订、计划版本/已完成数/剩余数（可用时）。
- 原因分类（不泄露内部密钥和敏感 payload）、失败尝试的存在、下一步需要用户做什么。
- 修订展示**实际持久化**的工具、版本和参数来源；不能使用模型口头解释代替保存计划。
- 已部分完成时只说“部分完成/已保存/暂停”，不说任务全部完成。
具体新增字段、响应兼容性留 Step 3 审核；不强制为每条动作开发新 HTTP endpoint。

## 3. 状态不变量与安全门禁（不可妥协）

I01. **唯一决策主链：** 仍是既有 Primary → Host → Registry/Executor → Observation → Host state；不存在第二套分类器、独立工具授权口子。

I02. **区分失败与未知：** `ok=false` 且 Receipt 已验证才允许进入失败重规划候选；没有 Receipt / 未确认结果只能安全停止或走受信恢复。不得因工具被描述成只读便绕过已有 unknown fence。

I03. **原子版本化：** 修订提交必须有 expected plan revision、ownership、Turn/Claim/Fence、一致的事务/CAS 防护。失败无部分写入；旧版本请求不得继续派发。

I04. **不可变来源：** completed step 的 dispatch/receipt/attempt/checkpoint 与 origin revision 不改写；跨 revision 引用必须由 Host 验证其源证据仍有效。不能只把全部旧 evidence 的 revision 字段替换成新版本号。

I05. **保留有效前缀：** 默认只能修订尚未完成的部分；不会因重规划重跑已完成前缀。若先前证据不再满足新目标，须显式记录失效/暂停，不能悄然复用；如何重建超出可安全范围的计划留下一轮明示方案。

I06. **依赖完整性：** 修订后图无环、无悬挂/前向依赖、输出类型/字段来源匹配；依赖只可读取可信已 checkpoint 输出；严格限定当前版本工具集合。

I07. **权限不扩张：** 仅限已经 Host-admitted 的受信只读工具合同，禁止任意写、动态 MCP discover、权限自动授予。

I08. **预算终止：** 当前 Turn 默认建议最多 `1` 次自动修订、最多 `1` 次由已验证失败引发的自动重复派发；超过预算退出并展示状态。确切预算配置在 Step 3 冻结；不会无界循环。跨 Turn 不能通过重置计数绕过同一失败的全任务预算。

I09. **成功≠完成：** 每个 step 的 verified-success 只证明那次工具执行和对应前置约束，不证明目标最终达成；S25-08 将负责更一般的终局验证。

I10. **安全降级：** 新能力 feature flag 关闭时，S25-06 已通过的 CREATE/FIRST/RESUME 语义和旧计划/普通 AgentLoop 行为保持不变。

## 4. 12 项验收用例：输入—预期行为—判定 oracle

| ID | 场景 / 受控前置 | 期望用户可见行为 | 确定性证据 oracle |
| --- | --- | --- | --- |
| R01 | 有 3 步、第一步成功 Receipt、后续可依赖其输出 | 继续合法未完成步骤，不改变成功证据 | 原 dispatch/attempt/observation 一致，新派发数量与 plan 对应 |
| R02 | 第 2 步已验证 `ok=false`，新合法参数可由可信用户/Observation 得到 | Primary 提议修订 pending，Host 通过后保存新 revision；执行仍须授权 | revision 严格增加、失败 Receipt 未丢、完成前缀保留、后继依赖新校验 |
| R03 | 同一只读工具可信失败，Host 明确 `retryable` 且预算充足 | 有界重新派发，失败仍报告失败，成功才 checkpoint | 新 dispatch/attempt ID；历史失败不可改；重试次数被计数 |
| R04 | 第 2 步的参数引用有效旧 Receipt，修订第 3 步 | 旧完成步骤无需重跑，第三步仍能在 Host 验证源输出后执行 | 保存的旧 source ID 不变；revised plan 有可信 origin 绑定 |
| R05 | 参数缺失/Schema 失配且无可信纠正来源 | 澄清或暂停，不能猜测参数派发 | 0 新 dispatch；状态未虚假推进；明确原因 |
| R06 | Registry 工具版本失配/关闭 | 拒绝执行并可提出合法替代；当前无真正等价替代则安全暂停 | 禁止使用未授权工具；无新增派发，已有完成状态不破坏 |
| R07 | Host permissions deny 或跨用户计划引用 | 不执行、不提升权限，给出安全错误 | 无任何新增执行及持久变更；拒绝来源 Host |
| R08 | pre-dispatch 已提交、Receipt 缺失 / controlled crash | 停止；不能推断失败并自动重试 | 同一/不同 worker、重复请求都不出现第二次调用 |
| R09 | 修订事务与 RESUME/另一修订并发（PG） | 仅一个写入版本可提交，失败者获得 conflict 并重新读取 | plan CAS + Claim/Fence + Turn 记录一致，无半提交/双派发 |
| R10 | 用户在已完成第一步后明确修改后续目标约束 | 仅修改 pending；若新目标否定既有输出则澄清/停止 | 目标来源绑定当前 Turn；完成记录不被自动改写 |
| R11 | 模型试图以工具文本中的“忽略授权”“替换成写操作”提案扩大权限 | Host 拒绝、稳定报告原因 | 无写入工具授权，无新执行/错误 plan 版本 |
| R12 | 主动连续失败/重复同样重规划/预算耗尽 | 有界结束，反馈部分完成和未解决项 | 自动重规划次数/执行次数不超过预算，跨 Turn 不无限复发 |

**R02/R03/R04 是核心正向证明**，R07/R08/R09/R11 是关键安全门槛。R06 不应制造假替代工具；若当前两工具没有有效替代关系，至少要可证明拒绝/保守降级，实际替代的正向能力必须另行明确范围和真实等价用例。

## 5. 验收证据分层与准入规则

### A. 静态 + 确定性（Step 4/5）
- Schema 和状态机转移、不可变来源、修订前/后校验、失败回滚、正确错误码；随机/边界输入；关闭开关回归。
- `R01-R12` 不得只依据 HTTP 200 或模型自述 PASS。每一项检查 DB 和派发数；尤其失败后没有意外重复调用。
- 用**真实生产路径中的受控故障注入工具适配器**产生可信 `ok=false` Receipt；Fake Provider/伪造 JSON 仅作为单测材料，绝不冒充工具链真实结果。

### B. 正式 HTTP + PostgreSQL（Step 5）
- 必须通过正式 `POST /conversations/{cid}/agent/messages` 和原有 owner/Claim/Fence 体系验证 R02、R03、R04、R07–R10。
- 用独立测试 PG、真实事务和多连接竞争、故障注入，确认 commit 前后状态一致；不得在生产库跑此类测试。
- 对 dispatch 已发而没有 Receipt、Receipt 已保存而 checkpoint 未完成、checkpoint 已完成而 Turn 未结束三个恢复分界分别验证安全性。

### C. 真实 Qwen（Step 5）
- 真实 `ollama_proxy/qwen3:14b` 通过正式 HTTP，冻结模型、Prompt、Flag、输入/输出摘要、Registry versions、依赖 plan 和 DB 证据；用未见过的话术变化检验模型是否提出合理动作。
- 至少 2 类可真实触发的失败/偏差场景、1 类有效保留前缀修订、1 类不能修订必须暂停、1 类目标改变、1 类 prompt injection/授权冲突；每类重复 >= 2 次并分别记录失败案例，不能只汇总通过率。
- **不可声称通用语义忠实保证**：S25-06 留有 1 strict xfail；新增重规划必须证明不扩大已有风险。若用户语义和实际计划不一致，不能靠模型自评直接认定成功。

### D. 集中退出标准（建议）
- P0 不变量 I01–I10 全部有针对性证据，无已知越权、未知重放、证据伪造、跨用户泄露、旧版本并发提交。
- R02/R03/R04/R07/R08/R09/R11 对应确定性和正式 API/PG 证据均通过；R05/R06/R10/R12 有明确边界与响应。
- 完整已有 pytest 回归和 CI 通过，新增真实模型挑战按逐用例 oracle 报告。真实模型的语义失败不能用“CI 绿色”掩盖；若正向核心反复失败，保持 VERIFYING 而不是宣告 CLOSED。
- 特性关闭时 S25-06 E3 / Host / PG 已有合同不回退，0 未授权新执行，`xfail` 如仍存在需准确标注来源而不是当 PASS。
- 收口仅可宣称 `S25-07 CLOSED — bounded observation-driven replanning for read-only dependency plans`，不得宣称任意工具 DAG 的通用自动化平台或生产-ready。

## 6. 阶段归属 / 不做事项
| 本阶段 S25-07 必须 | 明确后续 |
| --- | --- |
| 可信失败归因、预算内重试/继续/局部修订/澄清/停止、保留已完成来源、并发安全 | S25-08：通用目标最终验证与结果交付 |
| 已授权只读工具的实际合法替代（若有等价工具）或严格不支持+澄清 | S25-11：全局恢复/灾难恢复、worker-death operator 身份/外部 exactly-once |
| 当前 Turn 或安全续轮的修订状态与可观测行为 | S25-16：主动取消/暂停/撤销/交互任务控制全部能力 |
| 基础追踪和专项可复现真实模型评测 | S25-17/18：全局 trace 标准化及跨能力 benchmark |
| 不提升 Host 既有权限等级 | S25-12/13：扩展授权体系及任意 effectful / 医疗工具安全 |

## 7. Step 3 设计前的需验证决策
1. **默认执行策略：** 优先“同一已授权只读 Turn 内最多一次可信自动修订/重试，越界需下一轮准入”，不是“每次都必须用户确认”，更不是“自动无限循环”。Step 3 须确认旧 Primary admission 的 scope 是否可以合法覆盖此动作；不满足时使用先保存修订、下一 Turn 再执行，不能偷偷增加 authority。
2. **失败 Observation 对 Primary 的可见路径：** 当前 runner `blocked` 后直接退出；需要在**既有**路由/HTTP 主链中交付可信失败事实，不另起 Classifier。
3. **跨 revision 证据继承：** 目前 Host `revision == 1` 且源记录需同 revision。设计必须说明如何在不修改旧 Receipt 的前提下跨版本保存有效前缀，避免重复执行。
4. **局部变更粒度：** 优先保留已完成前缀和未完成后缀的局部修订；任何导致上游已完成证据失效的变更须 fail closed / 另立显式重建任务。
5. **替代工具证明：** `mcp_reference_lookup` 与 `text_stats` 不能凭“均只读”视为等价；没有真实可替代工具，就暂不承诺 replacement 正向通过。未经设计评审不可扩大工具注册表。
6. **单一错误分类合同：** Host 不根据模型自然语言为执行权限作决定；可信 error code、dispatch state、Receipt 真伪、已绑定约束由 Host 评估。

## 8. 下一步与审批门槛
本文件作为 Step 2 **审查候选**；需由用户确认/提出修改，随后进行 **Step 3 详细技术设计 + 独立复审**（revision schema + CAS/Fence + Primary/Host 接口 + 证据保存 + 状态机 + E2E）。未经 Step 3 设计通过，不进入 Step 4。分支/PR 保持 Draft；未经明确 `AUTHORIZE MERGE #49` 不合并。

**最终判定：Step 2 行为合同已提出，尚未实施、运行或验收。**
