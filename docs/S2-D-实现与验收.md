# S2-D — Planning / Replanning / Evaluation：实现与验收

## 第一条正式纵向切片（进行中）

继续沿用唯一 `POST /conversations/{cid}/agent/messages` → `AgentLoop` → `Tool Executor`，不新建独立 Agent 入口。

- `agent/planning.py`：Run 内存里的显式 PlanLedger；最多八步、单一活动 Revision、完成状态与证据工具、Revision 比对与完成进度保留。并不自动将模型宣称的成功写为完成。
- 三项模型可选择的受限工具：`propose_plan`、`revise_plan`、`complete_plan_step`。Schema 严格验证；未知工具 / 未暴露工具仍由 Executor 拒绝。
- 同一 AgentLoop 为多步任务提供规划 Skills Instructions；当工具失败时记录失败观察，模型可以保留已验证步骤并调整待执行计划。
- Agent 结果包含 `plan`、`plan_revision`、`plan_events`，可用于单次运行后审计；历史 `trace` 和 `audit` 沿用已有渠道。
- `evals/s2d_benchmark.py` 在离线可重放 Ledger 场景中对规划、缺证据拒绝、修订、过期 Revision 和终值逐项检查；属于**规则状态机评测**而非真实模型规划质量。
- `tests/test_s2d_planning.py` 验证已完成步骤不会被重规划丢失、非法完成与 stale revision 被拒绝、工具动态暴露范围。

## 必须保留的边界

这是 S2-D 的第一版实现，**不是阶段 CLOSED**：计划仍为单 Run 内存态，尚未跨对话轮次可靠持久化；成功工具观察只证明工具返回 `ok`，无法判定规划步骤语义确实完成；并行调用、时间超限/中断恢复、真实用户更正后的重规划和模型成功率基准仍待完成。

下一步需要真实 Qwen ReAct vs planning 对照，检验模型是否自主提议 / 修订计划，是否存在无意义的空规划和不必要步数增加，并基于多轮任务做持久目标延续。不得以本次状态机 pytest / CI 通过替代真实模型 E2E。

## 复现

```powershell
python -m pytest -q
python -m evals.s2d_benchmark
```

## S2-D 本轮真实 Qwen E2E 反馈修复

用户第一次真实 Qwen Trace：`propose_plan` 成功，`mcp_reference_lookup` 成功，`calculate` 一次失败，`revise_plan` 失败，再次 `calculate` 成功，但在未显示任何 `complete_plan_step` 时宣称已完成。该截图未暴露 revise 的原始错误码，不能反推具体错误原因。

- `revise_plan` 返回可区分的 `plan_not_created`、`stale_plan_revision`（携当前 revision）、`invalid_revision`（携 revision）；Pydantic 参数校验返回受限字段名及错误类型，不回显用户参数值。
- 若模型返回 final 时计划仍有 pending，统一 AgentLoop 将 status 置为 `partial`，在回复前附上明确的未验证步骤告知，不能标记为全部 `completed`。官方 Conversation API 接受并保存 partial 回答，不再误报 503 或丢失用户可读的部分结果；该回复不计入后续已完成消息历史。
- 新增正式 AgentLoop 负例，验证模型虚假完成被隔离、过期 revision 反馈、参数错误可诊断。

**仍待真实 Qwen 本地复验**：需查看后续失败原因与 PlanLedger 输出；Run 内单次 `last_success` 证据尚不能保证步骤语义相关性或多轮持久性。本修复属 S2-D IN PROGRESS，并不声称已有完整恢复链路。

## S2-D 执行证据与计划步骤绑定修复（同一 PR #11）

用户第二次真实 Qwen 验收：工具 RAG 查询和最终计算成功，但未调用 complete_plan_step，Agent 正确返回 partial。该结果验证状态防护，却揭示计划未随真实工具证据更新。

新增**显式验证绑定**：propose_plan/revise_plan 的 steps 在向后兼容字符串步骤基础上可传对象 `{goal,expected_tool,expected_arguments}`。仅受支持的只读工具支持绑定，真实执行观察同时满足 `ok=true`、工具名完全一致、参数精确一致，并且**只有唯一一个 pending 步骤匹配**，才由 PlanLedger 自动记录 completed。没有绑定的旧字符串步骤绝不会被工具成功自动推断完成。相同绑定对应多步骤时拒绝自动完成，避免模糊认领。提示词应引导模型对可验证步骤使用绑定结构。

重规划保留 completed 步骤；修订请求可省略 expected_revision，由可信执行层使用当前 Revision；如显式提供过期值仍予拒绝。失败时附当前 Revision 与重试引导，继续采用模型自行决定是否/如何局部重规划。新增整条 AgentLoop 的模拟决策 E2E：计划→参考查询成功→错误计算→局部修订→正确计算→最终 completed，及失败/歧义证据负例。

**仍待真实 Qwen 复验**：模型是否实际提供结构化绑定并成功调用 `revise_plan` 由本地 Qwen Trace 决定；通过 pytest/CI 不能冒充真实模型成功率。证据绑定是窄域参数匹配，不是通用目标语义验证；跨对话计划持续性和任务变更仍属于 S2-D 后续工作。

## S2-D 增量：真实失败恢复 Harness + 会话计划 Checkpoint

新增 `evals/s2d_live_recovery.py`，使用真实 Provider / 原 AgentLoop 的确定性可复现错误（calculate 8/0），观察模型是否自行 `revise_plan` 并以 calculate 8*8 恢复。该测试必须本地设置 `RUN_S2D_LIVE=1` 才运行，CI 只验自动化，没有用户的 Qwen 服务。与恶意故障注入不同，测试仅使用已允许的算术工具及可验证错误。结果保存于 `evals/results/s2d/qwen-recovery-v1.json`，失败不能掩盖。

跨消息会话计划引入 `conversation_plans` 的持久化迁移 `0004_conversation_plan` 和 `ConversationPlanStore`。正式 Conversation Agent API 仍是唯一入口，读取该会话的 checkpoint，恢复 PlanLedger / revision / 已验证步骤，并在 completed/partial 时保存。对后续用户更正，模型可以调用 `revise_plan` 修改未验证的待执行步骤；已完成步骤按原证据保留。恢复数据是数据库内由后端生成的快照，不允许用户任意传入。已有会话 Owner 校验仍执行。

当前版本的明确限制：首次实现尚未覆盖同一会话**并发消息**对 checkpoint 的竞态写入、用户更改**已经完成**的旧目标时的 supersede 治理、复杂多轮目标归属判断、崩溃前未落盘的 in-flight 状态恢复，以及全链路原子提交。此次开发不宣称这些已完成，也不将本地真实模型测试计为已通过。下一步应优先进行单会话竞态/覆盖保护以及真正用户更正的物理 E2E，之后再考虑 S2-D CLOSED。

### 用户更正：已完成步骤的显式废止

`revise_plan` 增加可选 `invalidate_step_ids`，允许在最新用户目标确有更正时指定哪些**已完成**步骤的旧证据已过期。执行层检查编号确为已完成步骤，拒绝未知/未完成/重复编号，保持未受影响的已完成步骤，再用新待执行步骤替换需要重做的内容；Revision 增加，并在 plan_events 记录废止的步骤 ID。测试覆盖“先查询 RAG、后算 8×8，下一轮更正第二步为 9×9，保留 RAG 进度”。

这并非自主识别用户目标更正的语义完备实现：是否调用此工具以及哪些步骤受影响由模型提出，执行层只验证结构和版本。更复杂的权限治理、并发消息协调以及步骤语义有效性仍需单独验证。

## 真实 Qwen 失败恢复：空 Trace 诊断修正

用户提供的 `qwen-recovery-v1.json` 显示五项任务检查全为 false、`plan=null`、`events=null`、`trace=[]`，因此先定位 Provider 链路，不能将其判定为模型无法重规划。真实网页多轮计划修订 RAG → 9*9 已成功。

`evals/s2d_live_recovery.py` 增加包装真实 `NativeToolDecision` 的 `DiagnosticDecider`，只记录模型决策请求次数、总决策耗时、Run 总耗时、Agent 原始 status / 终止原因、Prompt 版本、响应层网络/超时/HTTP状态错误类别及异常类名（无 URL、请求、密钥、原始回答）。同时给 all 工具策略显式使用 2000 字符 Skills Budget，避免当前默认预算影响评测，**不改正式会话 API 配置**。诊断测试覆盖 Provider 网络失败分类和正常无错误路径。

本地更新同一 PR 分支后执行：

```powershell
$env:RUN_S2D_LIVE="1"
python -m evals.s2d_live_recovery
```

输出仍保存在 `evals/results/s2d/qwen-recovery-v1.json`。只有获得明确的 Tool Trace / Agent 状态后才能判断失败恢复是否有效。CI 不执行真实 Qwen，不得将工具诊断代码的测试成功表述为真实恢复通过。

## 首次 Tool Decision 异常精细诊断（2026-10-20）

真实报告确认首次决策请求即 `provider_error`，1 次请求约 3.16 秒，`provider_failure.category=provider_response`，无任何 Tool Trace；这不能证明重规划能力失败。现 `NativeToolDecision` 定义 `ToolDecisionError(code)`（继承 ModelUnavailable），区分 `empty_decision`、`malformed_tool_call`、`invalid_response`、`provider_rejected`、`invalid_json_response`、`http_status`、`timeout`、`network`、`missing_credentials` 等，不保存原始响应或请求正文。原 AgentLoop 的 fail-close provider_error 行为不改变，避免 Provider 异常被误判为 Agent completed。

真实失败恢复 Harness 新增 `--strategy dynamic|all`，默认 dynamic；各自写入不同 JSON 文件，不覆盖已获得的证据。通过真实工具 `calculate(8/0)` 产生受控失败，仍要求模型自行 `revise_plan`、成功计算 64 和计划完成，不能伪造任何模型决策。需在用户本机真实 Qwen 运行：
```powershell
$env:RUN_S2D_LIVE = "1"
python -m evals.s2d_live_recovery --strategy dynamic
python -m evals.s2d_live_recovery --strategy all
```
测试成功前不得将真实重规划记为 PASS。CI 仅覆盖细分错误码和敏感信息不回显等确定性验证；策略间结果不做统计显著性宣称。

## 2026-10-20 首次空决策的兼容诊断与一次重试

用户真实 Qwen3:14b 两组数据 Dynamic 与 All 均在第一次决策发生 `empty_decision`，未产生工具 Trace。该错误只证明 Tool Call 适配器未收到可用 `content` 或 `tool_calls`，不能推断模型完全没有生成输出。

`agent/decision.py` 增加且仅针对**完全空决策**的一次受控重新请求（最多两次模型决策 HTTP 请求；绝不重试已观察到的 Tool Call，也不自动重试 malformed、超时、HTTP 等其他错误）。第二次依旧空时 fail-closed，返回 `ToolDecisionError("empty_decision")`。同时记录最后一次响应的**脱敏结构指标**：message 是否存在、content 字符数、tool_call 数、thinking 是否非空、白名单化 done_reason、重试次数。绝不读取、保存或将 thinking 视为最终回答/工具调用；不在日志内暴露思维内容、原始响应正文、网关 URL 或密钥。

`evals/s2d_live_recovery.py` 对此报错写入 `provider_failure.response_shape`。自动化覆盖空后成功、连续两次仅 thinking 的 fail-closed、Malformed Tool Call 不重试。CI 成功仅表示适配器行为可靠，不表示用户本地模型真实失败恢复已经通过。

复验：`$env:RUN_S2D_LIVE="1"` 后分别执行 `python -m evals.s2d_live_recovery --strategy dynamic` 与 `python -m evals.s2d_live_recovery --strategy all`，检查生成的 v2 JSON。只有真正进入工具执行并看到受控失败→模型重规划→成功恢复的证据才算 PASS。

## Provider 空决策：停止补丁式猜测，先完成四级控制变量矩阵（2026-10-20）

真实 Qwen 两次重试后仍返回 `message_present=true`, `content_chars=0`, `tool_call_count=0`, `thinking_present=false`, `done_reason=stop`，dynamic 和 all 一致。该结果不支持进一步添加重试，也不支持宣称重规划模型失败。网页成功而故障恢复脚本失败的关键差异是复杂故障指令和暴露的工具/Schema，尚未有独立控制变量证据。

新增独立的 `evals/s2d_provider_compatibility.py`，使用**正式 NativeToolDecision 适配器**、相同系统 Prompt 和真实 Provider 顺序发起四种只读决策请求：(1) 纯文本且无工具，(2) 单 `calculate`，(3) `propose_plan`+ `calculate`，(4) 原完整失败恢复目标与对应 Planning Tool Schemas。此诊断**只观察 Tool Decision，不执行任何返回工具**；不记录 Prompt 原文、回复、Thinking、凭证或网关地址。每个案例分别输出请求耗时、Prompt 长度、工具数、可用内容与 Tool Call 数量、错误码/响应结构。不会把诊断矩阵“通过”误计为失败恢复成功。

本地运行：
```powershell
$env:RUN_S2D_LIVE="1"
python -m evals.s2d_provider_compatibility
```
生成 `evals/results/s2d/qwen-provider-compatibility-v1.json`。根据报告判断：纯文本也空→模型/网关基础链路；只有工具请求空→Tool Calling 适配与网关；仅计划工具空→计划 Schema 兼容；仅复杂目标空→指令复杂度/模型行为。定位后再做最小修复并继续原失败恢复脚本验证。PR #11 必须保持 Draft。

## 2026-10-20 真实 Qwen 兼容实验反馈与匹配请求复验

用户在 Windows 上提交 `qwen-provider-compatibility-v1.json`：plain_text_no_tools=decision(文本), calculate_only=decision(calculate), plan_and_calculate=decision(propose_plan), recovery_goal=provider_error(empty_decision)。各案例仅一次，不是稳定性结论。该结果直接排除“所有请求均无法连接”和“所有 Tool Calling 均不兼容”；**没有**证明恢复指令必然导致空回复，也没有证明模型无法修订计划。

审查发现初版矩阵随每条 goal 动态选择 Skills Instructions，故此前“复杂恢复指令”与“简单规划指令”对照**不只更改用户消息**。新增同一脚本中的 fixed_simple / fixed_failure / fixed_original 三个控制案例：共享与 recovery_goal 一致的 system prompt、三项原生 tool schemas、Provider settings，只更改用户目标。新增测试验证三例请求条件一致，以及 fixed_original 与原始 recovery_goal 精确一致。CI 仅检查程序，**不能替代真实 Provider 结果**。

本地复验：`$env:RUN_S2D_LIVE="1"; python -m evals.s2d_provider_compatibility`，读取 `evals/results/s2d/qwen-provider-compatibility-v2.json`。重点比较 fixed_simple、fixed_failure、fixed_original；若只有 fixed_original 失败，再逐段简化原复杂任务并保留固定请求条件。若 fixed_simple 也失败，不能归因于故障语义，应检查后续运行的服务状态、模型生成输出参数与请求间波动。

当前**未修复**真实失败恢复：四组首次证据只是 Provider 预决策，不执行真实工具。下一次决策应基于匹配实验结果确定最小原因，然后再运行 `python -m evals.s2d_live_recovery --strategy dynamic` 验收完整 Observation → revise_plan → calculate 恢复链路。PR #11 保持 Draft，不得未经授权合并。

## 2026-10-20 v2 固定请求真实 Provider 实验与 E2E 最小修订

用户上传的七案例 v2 报告显示：`fixed_simple`=decision(propose_plan)，`fixed_failure`=decision(propose_plan)，`fixed_original`=provider_error(empty_decision)。固定三例采用相同 system、三项 tool schemas、Provider 配置，只有 user goal 不同。短失败恢复描述已能在第一次真实模型调用中输出工具调用；未证明后续执行和真实恢复成功。原始指令失败可能与其长度或具体指令组合有关，目前无法区分，不能宣称找到了 Provider 根因。

仅将 `evals/s2d_live_recovery.py` 的 E2E 目标改成已验证会产生 `propose_plan` 的 `fixed_failure` 短描述，不修改正式 AgentLoop/Provider，保留原有失败观察、模型修订、计算成功、计划全部验证的五项判定。报告路径升级为 `evals/results/s2d/qwen-recovery-dynamic-v3.json`（或 all-v3），避免覆盖旧证据。后续需在用户 Windows 真实 Qwen 环境运行 `$env:RUN_S2D_LIVE="1"; python -m evals.s2d_live_recovery --strategy dynamic`，将报告反馈用于下一次有因修复。CI 仅验证代码行为，不能宣称真实恢复 E2E PASS。

## 2026-10-20 真实 Qwen 受控失败恢复 v3：首次 E2E PASS

用户提交本地 `qwen-recovery-dynamic-v3.json`，记录 REAL_PROVIDER / ollama_proxy / qwen3:14b / dynamic。五个报告检查均为 true：`observed_failure`、`model_revised_plan`、`observed_recovery`、`terminal_completed`、`all_plan_steps_verified`。真实执行 Trace 顺序：第 1 步 `propose_plan` 成功；第 2 步 `calculate` 失败；第 3 步 `revise_plan` 成功；第 4 步 `calculate` 成功；第 5 步模型结束并返回 completed。计划 Revision 1 → 2，最终一步 `expected_tool=calculate`、`expected_arguments.expression=8*8`、`evidence_tool=calculate`、状态 completed。共 5 次模型决策，总耗时 5273 ms，`provider_failure=null`。

**证据限制**：失败事件报告为 `invalid_arguments`，当前保存的 Trace 并未包含失败调用的原始参数，因此只能验证真实工具失败→模型修订→成功恢复，不能断言实际触发了算术除零异常或精确判断原始错误参数。单次成功不代表可靠性百分比或复杂任务泛化；这不是正式 Conversation API 跨轮数据库一致性测试。此前长指令导致 `empty_decision` 的具体模型/Proxy 根因尚未确认，不能将简化测试目标描述为 Provider 根因修复。

此项真实模型恢复链路可标记 **PASS（单次受控实验）**，但 S2-D 整体仍 **IN PROGRESS**。下一步仅在必要时检查失败调用参数是否确为 `8/0`、错误语义，再选择下一项实际纵向功能，不启动新 Runtime 架构。对应先前代码 HEAD `8d0cb23d38807754283cbcf8d078c1c89e300c30` 的 CI #39384712114 SUCCESS。

## 同一会话并发 Turn 准入保护（2026-10-20）

正式 `POST /conversations/{cid}/agent/messages` 原实现会允许两个并发请求同时加载相同 `ConversationPlanStore` Checkpoint，并分别写回最后完成的快照，存在“旧计划覆盖新计划”的真实竞态。

新增 `0005_active_turn_guard`：在现有 `turns` 表创建仅对 `state='running'` 生效的 `conversation_id` 部分唯一索引（SQLite / PostgreSQL），利用数据库事务中的 Turn 插入实现跨进程原子准入。同一会话已有 running turn 时，新的独立请求在加载 Checkpoint / 执行 Agent 前返回 HTTP 409 `conversation_turn_in_progress`；不同会话可并行，已结束状态释放索引位置。已有 request_id 幂等冲突继续使用 `duplicate_request_id`。测试验证同会话拒绝、跨会话允许、终态释放后重入。

**限制与部署风险**：这是一项“拒绝同会话并发”，不是请求排队或全链路原子持久化。异常中断后遗留的 `running` Turn 可能阻止新请求；当前没有 TTL / 后台恢复机制，需运维审查并治理停滞的 Turn。迁移前如存在多个相同会话的 `running` Turn，数据库将拒绝创建唯一索引；不得自动删除或改写未核查的历史记录。SQLite 单写者争用仍可能表现为 database locked，而不是始终为 409。工具的外部副作用和 Plan Checkpoint、assistant 消息仍未实现同一事务提交。S2-D 继续 IN PROGRESS；CI PASS 不代表多 worker + 真实 Qwen 物理并发测试已执行。

## Running Turn 崩溃恢复与数据库提交一致性（2026-10-20）

CI 回归发现 `tests/test_migration.py` 仍期望旧 Alembic 版本 0004，实际版本已升级至 0005；已按新增索引修正断言。

**本次增量：**
- `services/recover_turns.py` 提供默认仅查看的人工恢复工具，扫描超过指定时长且 assistant placeholder 仍存在的 `running` Turn。只能在**停止所有 API worker、确认无在执行工具**后加上 `--apply --confirm-workers-stopped` 将其标为 `interrupted`，保留原请求及 Checkpoint，不自动重放、不给工具补偿、不标记成功。年龄阈值只是人工筛选条件，不是自动抢占权威；缺少对应 assistant 时间戳的异常数据不自动处理。
- API 成功结果的 `conversation_plans.snapshot`、assistant message 状态和 `turns.state` 改为**同一个 SQLAlchemy 数据库事务提交**，若事务失败一并回滚，向客户端报告 503 `turn_finalization_failed`。仅一个会话 running Turn 的数据库索引仍有效，终态时释放。
- 新增恢复路径测试；真实模型恢复原有证据保留。

**不可扩大声明：** 这解决的是本地 SQL 数据库中三个记录的原子提交，**无法**将运行过程中已经发生的外部工具调用、用户记忆/任务工具写入纳入同一事务，也不能在崩溃后证明外部副作用没有发生。没有引入 Outbox、补偿执行、分布式租约或自动重试，避免误报 exactly-once。恢复命令要求停机维护，不适合不经人工审查的生产自动运行。

停机确认后本地操作示例：
```powershell
python -m services.recover_turns --minimum-age-minutes 10
python -m services.recover_turns --minimum-age-minutes 10 --apply --confirm-workers-stopped
```
执行前核对 `DATABASE_URL` 指向正确数据库并备份。旧进程仍运行时不得执行 --apply。此次为 S2-D 渐进切片，CI/真实多进程崩溃物理验收状态需分别记录。

## 2026-10-20 正式 Conversation API 真实 Qwen 多轮验收入口

新增 `evals/s2d_conversation_api_live.py`。此工具**不创建第二条 Agent 执行链**：通过 FastAPI `TestClient(create_app(settings))` 真实调用唯一 `POST /conversations/{cid}/agent/messages`；其 `NativeToolDecision` 指向用户配置的真实 Provider，数据库固定使用自动清理的临时 SQLite，不访问生产/日常工作数据库，不使用 Mock 决策。自动创建会话→第一轮规划计算 8*8→检查持久 checkpoint→第二轮更正为 9*9→检查 revision 前进、9*9 完成证据、8*8 旧完成结果已废止、两轮消息与 Turn 终态。报告不导出原始回复、凭证或完整用户消息。任何一步失败仍生成可供定位的 JSON；该脚本单次运行不代表可靠性统计或 PostgreSQL/多进程物理验收。

Windows（使用本地 `.env` 配置的 qwen3:14b）：
```powershell
$env:RUN_S2D_LIVE = "1"
python -m evals.s2d_conversation_api_live
```
报告：`evals/results/s2d/qwen-conversation-api-v1.json`。检查失败项与 `trace_tools` 后针对确切证据修复；**未执行此本地真实 Provider 脚本前不能声称多轮真实 API PASS**。这是进程内 ASGI HTTP 接口级 E2E，而非浏览器或独立 Uvicorn 进程验收。S2-D 仍 IN PROGRESS。

## 真实 Qwen 多轮 API v1 反馈与 v2 诊断（2026-10-20）

本地真实 `qwen-conversation-api-v1.json` 为 8/9 PASS：两轮 HTTP 200，`propose_plan → calculate` 与 `revise_plan → calculate` 均执行，checkpoint Revision 1→2，9*9 步骤完成；唯一失败项为 `old_calculation_invalidated=false`，说明旧 8*8 completed 证据仍在最终 checkpoint。v1 报告没有修订的 invalidated / preserved 详情，不能据此直接确定是模型漏传 `invalidate_step_ids`，还是执行层故障。

修复 E731 测试语法并将报告升级为 v2，增加脱敏的 `revision_diagnostics`（revision、invalidated、preserved）和 `final_plan_steps`（id、status、expected_tool、expected_arguments）。不改变核心 `PlanLedger` 的默认保留已完成步骤机制，避免为了测试通过而自动废止历史证据。Windows 更新分支后运行 `$env:RUN_S2D_LIVE="1"; python -m evals.s2d_conversation_api_live`，读取 `evals/results/s2d/qwen-conversation-api-v2.json`。在 v2 实证出来之前继续标记多轮更正最终验收 FAIL / S2-D IN PROGRESS。

## 2026-10-20 真实 Qwen 多轮更正 v2 根因收窄

用户上传的 `qwen-conversation-api-v2.json` 再次显示 8/9 PASS。第一次 `propose_plan → calculate(8*8)`，第二次 `revise_plan → calculate(9*9)`；Checkpoint Revision 1→2。修订事件 `invalidated=[]`、`preserved=1`，最终仍有 completed 的 8*8 和 9*9 两步。因此可确定执行结果未废止旧完成步骤，但报告未含原始模型工具参数，不能独立断言模型确切传了空数组或省略字段。

现有 core Prompt 已包含 `invalidate_step_ids` 的原则说明；此轮只在按目标选取的 Planning Skill 指令中补充“用户明确替换已完成目标时应传被替换步骤 ID，其他步骤保留”，并增加静态回归测试；不改变 `PlanLedger.revise()` 默认保留完成证据的规则，也不让 Harness 自动注入废止 ID。**尚未做真实 Qwen 复验，不能标记缺口已解决。** 使用已有 `python -m evals.s2d_conversation_api_live` 和 `qwen-conversation-api-v2.json` 作为下一次真实测试。单次生成不同结果也不代表稳定性已经建立。

## 2026-10-20 目标更正完成状态保护（最小范围）

Qwen v2 实际复验仍 `invalidated=[]` / `preserved=1`，说明指令增强不能稳定促成旧证据废止。已在 AgentLoop 中添加**仅针对明确算术替换表达式**的保守最终状态校验：当本轮话语出现“不是旧表达式，而是新表达式”、此前 checkpoint 存在同表达式、同工具绑定的 completed 步骤时，最终检查该**旧表达式绑定**是否仍保留 completed。如果保留，改为 `partial` 并明确提示旧证据尚未失效；不静默移除工具证据、不自动代模型决定废止、也不改动 PlanLedger 修订行为。对“在原计算基础上再算新表达式”之类追加目标不触发。检查按旧表达式绑定而非只看 ID，避免 revise_plan 重编号导致误拦截。

**重要限制**：这是非常窄的算术演示防误报保护，并不是通用自然语言替换意图理解器。它阻止本例错误声称 completed，但**不会令 9×9 更正案例自动 PASS**；模型仍需要正确提供 invalidate_step_ids。后续完整语义理解/冲突确认需单独设计，不应将本校验宣称为通用 Agent 目标一致性机制。相关回归见 `tests/test_s2d_goal_correction_guard.py`，本地真实 Provider 重新执行后需观察第二轮是否从 completed 转为 partial，以及其余工具轨迹是否保持正常。

## 2026-10-20 更正废止缺口：工具级拒绝与模型重试

此前真实 Qwen 连续多次给出 `revise_plan`，但 `invalidated=[]`、`preserved=1`，使旧 8*8 成功证据仍保留。仅增加 Core Prompt / Planning Skill 未改变这一行为；随后最终态降级为 partial 只防止误报，没有促使模型修正工具请求。

本次将已存在的显式算术更正 guard 提前到 AgentLoop 的 `revise_plan` 工具准入处：**当已有匹配旧表达式的已完成 checkpoint 步骤而模型未提供对应 `invalidate_step_ids` 时，返回 `missing_required_invalidation`、`required_step_ids`、原 revision，并给出可操作重试提示**。拒绝发生在 `PlanLedger.revise()` 之前，计划不发生修改；必须由模型再次提交完整修订，不由执行器静默补全参数。新增脚本化多次模型决策测试覆盖：缺失废止 → 工具拒绝 → 修正为 [1] → 修订成功 → 9*9 工具验证 → completed。

该规则只对精确识别的“不是旧算术表达式，而是……”匹配旧 checkpoint 的情况生效；追加任务、无历史绑定的情形不拦截。**尚未证明一般语言的目标变更可自动识别**。真实 Qwen 再测可继续执行 `python -m evals.s2d_conversation_api_live`，报告 `qwen-conversation-api-v2.json`，本次关键关注 `revision_diagnostics.invalidated` 是否变成 [1] 及工具反馈后是否二次 revise_plan；如模型不重试，只能报告 partial，不代表修复完全成功。S2-D 不在未取得真实模型 PASS 前 CLOSED。

## 2026-10-20 S2-D 最终回归矩阵（合并前）

同一 PR #11 收口，不修改已获得真实 Qwen 9/9 PASS 的主链。补充 `tests/test_s2d_invalidation_regression.py`，覆盖错误编号 [99] 经 AgentLoop 工具入口被拒、revision 仍为 1、模型改用 [1] 修订后成功、旧绑定废止且新 9*9 工具绑定完成。

| 回归范围 | 证据 |
|---|---|
| 追加任务保留旧证据 | `tests/test_s2d_goal_correction_guard.py` |
| 无关目标更正不误废止 | `tests/test_s2d_goal_correction_guard.py` |
| 无效完成步骤编号、旧修订拒绝 | `tests/test_s2d_resume.py` / `tests/test_s2d_invalidation_regression.py` |
| 真实模型工具失败恢复 | 已上传 `qwen-recovery-dynamic-v3.json`：单次五项通过 |
| 真实模型跨轮目标替换 | 已上传 `qwen-conversation-api-v2(3).json`：单次九项通过，第二轮 revise_plan 两次，最终 invalidated=[1] |
| 正式 API 本地并发/持久化 | Windows 四项 pytest 通过；GitHub CI 的相关门禁通过 |

**决策边界**：这些证据支持受控 S2-D 算术计划恢复/更正与既有回归；不等于通用自然语言目标识别、外部工具 exactly-once 或长期稳定性。最新精确 HEAD 的 CI 要单独确认 SUCCESS；若全绿，可推荐 PR #11 进入合并前最终审查，但不自行合并。
