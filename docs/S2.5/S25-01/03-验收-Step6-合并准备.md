# S25-01 Step 6 — 最终收口、遗留登记与合并准备（2026-10-22）

## 1. 适用范围及结论

范围：PR #17 / feat/s25-01-unified-understanding-slice1-20261021，目标分支 main。当前结论是 **STEP 6 DOCUMENTED / MERGE PENDING AUTHORIZATION**。这不是生产默认切换许可，也不是 PR 已合并。用户确认 12/12 人工测试已经执行：11/12 完全通过，M05 基础通过但存在 P2 能力缺口。细节见同目录 `02-测试验收与修复.md` 第 3 节。

## 2. 实施结果

S25-01 当前包含模型结构化 Understanding、GoalSpec 与 change 分类、light/strong 策略和有界重试、上下文输入、Execution Policy 与 Host 工具治理分界、Goal Store 状态和数据库迁移、Agent Loop 接线、工具/状态回归测试以及真实 Qwen 诊断和人工验收路径。保留 UNDERSTANDING_STRATEGY=direct 默认行为，structured 作为显式测试与试用策略。不得仅凭 12 例通过声明生产级稳定性。

## 3. 人工测试与证据

- 用户在 Windows 本地真实 Qwen 环境完成 M01–M12，确认只有 M05 的跨轮约束冲突处置尚不理想。
- M03→M04 截图证明文本草案第二项修改、第一和第三项保留，不声称保存；最初 HTTP 503 已修复。
- M05 截图显示保留旧三项、追加“周末买菜”为第四项，未说明“最多三项”旧约束与新指令相互矛盾。
- M09/M10 数据库与聊天记录证明“周五提交测试报告”创建且查询返回相同 ID。
- M11 不勾选授权时，重测截图显示正常拒绝声称保存；早期理解异常不代表新版本仍失败。
- M12 用户确认授权写入测试通过，SQLite 中存在 manual_code=QA-17；不把历史最终快照当作精确请求级写入日志。
- User-local SQLite 记录含真实历史多轮测试，未上传完整逐项请求/Trace 和数据库快照；文档引用人工反馈时标明此限制。
- 先前 eval 报告 Structured/Direct 各 5/5 completed 不是语义完整性证明。Direct propose_plan 遗留问题不能默认为关闭。

## 4. 遗留缺口登记

| 编号 | 优先级 | 问题 | 安排 | 退出标准 |
|---|---|---|---|---|
| S25-01-GAP-01 | P2 非阻塞 | 多轮中旧约束“最多三项”，新请求“保留三项再加一项”，系统执行四项却未说明旧约束已覆盖或需要澄清 | 后续统一约束状态与动态任务/计划治理，不加关键词补丁 | 用显式约束版本、覆盖依据、冲突解释或澄清路径进行多轮 E2E；验证状态持续继承与无未授权副作用 |
| LEGACY-DIRECT-01 | 独立质量遗留 | Direct 多轮失败 propose_plan 仍返回 completed | 单独 Agent Loop/Eval 改善时处理 | 工具失败不会被错误报告为真实成功；有 Trace 和真实回复的回归 |
| STRUCTURED-CONSTRAINT-01 | 技术债 | 真实模型 add/revise 初次可能 missing_goal，靠重试恢复 | 后续模型输出可靠性评测 | 记录模型/Schema 兼容性、首轮成功率及异常监控，不能仅报告最终成功率 |
| ACCEPTANCE-EVIDENCE-01 | 证据增强 | 本轮以人工确认/截图/SQLite 查询为主，缺全量逐会话请求 Trace 与 DB 前后 diff | 下一次集中回归纳入可脱敏结构化导出 | 每项用例具时间、HEAD、模型、Trace、状态、指定会话差分 |
| S25-01-GAP-02 | 设计对照，非本轮合并阻塞 | 最小对象比设计 3.2 瘦。GoalSpec 无 goal_id、open_questions、context_refs，版本只在 conversation_goal_states.version。ConstraintSpec 无 validity，相对时间「明天」「周五」没有可信时区解析。GoalChangeProposal.delta 是字符串，无结构化 proposed_delta、target_step_refs、rationale_codes。ClarificationDecision 无 blocking_fields。UnderstandingResult 无 failed、provenance、understanding_model_version，失败以 UnderstandingError 抛出 | 后续补契约时区分仍属 S25-01 的最小字段，以及留给 S25-03/09/17 的字段。本轮不直接加字段 | 模型不能伪造权威 ID 或版本。无时区时相对时间保持原表述或澄清。失败有稳定错误码。理解记录能关联模型版本，且默认不落私密原文 |
| S25-01-GAP-03 | 设计对照，非本轮合并阻塞 | add 用整份候选的 objective 相等和子目标前缀对比来阻止静默覆盖，不是设计中的独立增量。宿主仍接受模型重述后的完整目标 | 与第 8 节的新增、取消、局部更正和 PlanLedger 专项 E2E 一起做增量合并 | 追加只表达新增项。旧子目标和显式约束由已确认状态保留，不依赖模型重述整份目标才算通过 |
| S25-01-GAP-04 | 设计对照，非本轮合并阻塞 | 设计情景 U01–U15 没有作为固定验收集执行。现有证据是 10 条理解评测和 M01–M12。U10 中英混合、U11 工具失败后换办法、U12 把引用文本当作用户指令，没有对应用例结果 | 下一轮真实模型回归补同义改写、缺失上下文和对抗变体。不为原句增加关键词补丁 | 每个情景有真实模型结果，负例没有发生，失败单独记录 |
| S25-01-GAP-05 | 移交 S25-04，非本轮合并阻塞 | agent/skills.py 的 select_skills 仍按中英文关键词匹配用户原文。structured 路径把理解结果交给 Agent Loop，技能过滤仍看原文。设计把语义工具曝光划给 S25-04 | S25-04 改为消费已校验目标，并写明关键词过渡何时移除。S25-01 不新增关键词分支 | 工具曝光依据校验后的目标语义。关键词表不再是主选择器，或明确登记为临时兼容及移除条件 |
| S25-01-GAP-06 | 设计对照，非本轮合并阻塞 | direct 且没有理解结果时，agent/loop.py 仍用正则或词表处理算术「不是…而是」、继续未完成计划、固定记忆问句和「只做第一步」。structured 传入理解结果后跳过这些分支。第 8 节只要求旧特判有退出证据，此前没有点名 | 把 structured 设为默认前，用泛化回归证明这些行为已由结构化理解覆盖，再移除对应分支。权限、幂等和工具校验不随语义特判删除 | 主路径不再靠这些句式分流。旧测试的等价行为有新证据。理解失败不回退到关键词执行 |

## 5. 变更及安全边界复核

- respond_only/read_only 不应写入 Goal Store、任务或记忆，允许消息和 Turn 审计保存。
- 保存记忆是独立工具动作，不能错误要求已确认的持久化目标；记忆保存还受单次 write consent。
- 无已确认目标的 revise 若是真正持久目标变更应受控澄清；文本草案修改不应依赖 Goal Store。
- 生成模型的结构化提议不能直接成为工具授权，执行许可由 Host 决定。
- 用户确认正常的人工测试并不替代负向授权/数据库回归和 CI。

## 6. 合并门禁

1. 本文件与 Step5 最终人工验收记录在 PR #17 功能分支。
2. 对**文档提交之后的精确 PR HEAD**检查 GitHub Actions，必要时排查失败并再验。
3. 检查 PR 仍以 main 为基线、无冲突/阻塞检查，最终 PR 描述应准确说明最新完成范围（初始 Draft 文本已过时）。
4. 在用户明确给出合并授权之前保持 PR OPEN / DRAFT，不绕过保护规则，不执行任何 Merge。
5. 合并获得授权后还必须按当时最新 SHA 再核对 CI 和 main，合并后再做 Post-Merge Verification。

## 7. 阶段裁决

**S25-01 开发与人工测试进入 Step 6 收口；非阻塞 P2 有明确后续归宿。** 第 4 节后五行是对照设计稿补记的契约与覆盖缺口，不改变本轮有条件通过的判定，也不是新的合并许可。当前文件完成不等于自动合并完成。最终状态以 PR 当前精确 HEAD CI 和单独合并授权为准。

## 8. 默认策略切换条件

`UNDERSTANDING_STRATEGY=direct` 保持默认。`structured` 只在显式配置下试用。切换为默认前同时满足：

1. 真实模型任务通过预先约定的准确性、误澄清、漏约束与越权行为门槛。明确约束更正和有副作用的任务要有确认，不能靠模型猜测确认。
2. 记录完整请求的延迟与资源，包括两个模型同时加载和换载的代价。小模型单次更快，不等于整轮更快。
3. 旧 S2 与 S25-01 回归通过，旧特判逻辑有退出证据。关键词理解器不与模型理解长期并存。
4. 在受控环境演练 `UNDERSTANDING_STRATEGY=structured`，复核后再改配置默认值，并保留紧急回滚。

`GoalSpec` 的新增、取消、局部更正与 PlanLedger 的对齐仍要专项 E2E。结构化 GoalState 不能单独证明旧计划步骤已失效，或外部副作用已回滚。

## 9. 评测命令与结果路径

`evals/s25_01_live.py`、`evals/s25_01_local_e2e.py` 与 `evals/s25_01_agent_live_compare.py` 共用 `evals/s25_01_paths.py`。`--output` 只给文件名时，结果写入 `evals/results/s25_01/`。仓库已忽略 `evals/results/`。

```powershell
# 在 D:\HealthAgent 执行；配置优先从 .env 加载
python -m evals.s25_01_live --output s25_01_live.json
python -m evals.s25_01_local_e2e --output s25_01_e2e.json
Get-ChildItem .\evals\results\s25_01\*.json
```

原生 Ollama 适配器把 `MODEL_BASE_URL` 拼成 `<BASE>/chat`。使用 `ollama_proxy` 时设为 `http://127.0.0.1:11434/api`，实际请求 `/api/chat`。不要把该变量设成 `http://127.0.0.1:11434` 或 `/v1`。代理服务按其协议选择 `MODEL_PROVIDER`。

理解评测不覆盖完整 Agent 延迟。E2E 小样本受模型缓存和换载影响，至少区分冷启动与热启动，并注明设备。这份样本不能单独决定默认切换。旧 JSON 若在项目根目录，需手动移到 `evals/results/s25_01/`，脚本不会自动迁移。
