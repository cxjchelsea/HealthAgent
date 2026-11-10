# S25-06 Step 2 — 独立式代码契约复审与裁决

- 2026-11-10；main@9e2e79a；审查 PR #35 文档 HEAD 3709f2b
- 范围：静态独立视角的文件交叉核验，不冒充第三方代码 Review 或 PostgreSQL/真实模型物理验证
- CI：提交 3709f2b 对应 GitHub Actions run 42744468541 状态 success；仅证明该提交原有工作流通过，非新功能验收
- 裁决：**DESIGN ACCEPTED WITH REQUIRED CORRECTIONS**，在 Step 3 先解决下列阻塞项；禁止直接实施 Step 4

## 复审证据

1. `agent/planning.py`：`PlanLedger.restore` 只接受 pending/completed；`observe` 按工具名和参数匹配，若不同计划步骤重复同一工具/参数会形成歧义；`checkpoint` 未定义 DAG 版本。**新状态或依赖字段不能作为旧代码默认可理解的语义。**
2. `agent/plan_tool_versions.py`：`legacy-plan-v1` 绑定的是工具执行语义，历史无版本仅在同版 legacy 运行器下兼容。**Plan 格式版本必须与执行器版本分离。**
3. `agent/plan_invocation.py`：`inspect_plan_invocations` 对所有 pending steps 预先验证并生成 digest，`execute_first` 截断一项；其描述不授予执行权。**动态参数在前置校验时可能尚未解析，需设计两阶段绑定审查。**
4. `agent/host_plan_execution_binding.py`：Host binding 用全 checkpoint digest、revision 和 immutable invocations；对于执行前尚未可解析的下游参数，需将“计划模板核验”与“本次 eligible step 具体参数核验”明确区分，不能绕过绑定。
5. `agent/plan_read_invoker.py`：只接受完全验证的具体 arguments，在 Guard 之后执行 M4 Dispatch/Receipt；`services/plan_dispatch.py` 要求 checkpoint step 的 expected_arguments 与 Dispatch 完全一致。**不能在上游观测出来后隐式变更 expected_arguments 而丢掉修订/来源记录。**
6. `services/s25_03_projection.py`：投影 checkpointed 的 M4 receipt，并关联 Attempt，但不是所有返回字段都具有可用于再调用的类型、完整性或隐私许可。**不能直接把 read-only evidence API 当成动态输入源。**
7. `agent/loop.py`：保存计划分支按 pending 列表执行，execute_first 限一步；失败停止，有 M4 时不会自动发起无日志恢复。**06 继续沿用这一安全默认。**

## 必须在 Step 3 消除的设计冲突

- **R1 / P1：新旧执行器隔离。** `dependency-plan-v1` 需明确解析入口、schema validator、feature flag、旧计划识别和 fail-closed 规则；禁止将新字段直接塞进旧 `PlanLedger.restore` 后调用旧 resume。
- **R2 / P1：动态引用与完整证据。** 先确定单一受信结果来源及 owner/conversation/plan/step/revision/dispatch/version 的绑定；验证 receipt 为 checkpointed 且允许字段有持久化记录。可利用 M4 Receipt，不能依赖 prompt 或 model Trace。
- **R3 / P1：执行实例 ID。** 结果必须以 step_id + dispatch_id + args digest 与 checkpoint 关联；不能延用旧 `observe(name,args)` 唯一性匹配作为依赖步骤完成判断。
- **R4 / P1：两阶段 Host 绑定。** 创建/恢复先验证完整计划模板和能力集合；每次调用前解析受信参数，执行完整 Pydantic/权限/版本/Claim 校验。不可只验证第一步而对后续未知工具默认授权。
- **R5 / P1：first/resume 语义固定。** 创建时要求计划拓扑有序；first 只执行列表中的首个未完成且 ready 的步骤，不主动补前置依赖；resume 逐个按拓扑次序，失败默认停止。
- **R6 / P1：状态与修订。** 保存计划快照中的长久事实与临时推导的 ready/running 不能混淆；失效引用及 revision 升级须避免重用旧 Observation。
- **R7 / P2：最小模型能力证明。** 现有 26/26 05 验收不是 06 DAG 能力证明；应补真实模型多样任务与局部故障恢复。

## D1–D7 复审建议

| 决策 | 建议 |
| --- | --- |
| D1 新版计划承载 | 单独依赖 Schema/Adapter，共用单一 Host/M4 dispatch；旧 PlanLedger 只负责 legacy |
| D2 输出白名单 | 首版只支持受信 MCP `definition` 字段作为 bounded string；`source` 仅用于溯源不作为任意命令输入；其他数据类型后续按测试增加 |
| D3 版本 | plan_schema_version 与 executor/tool_version 解耦，old absent -> legacy-only，unknown -> deny |
| D4 原子性 | 读取上游已 checkpointed Receipt + checkpoint 关联；新步骤完成必须继续保留现有最终化事务 CAS 和审计投影 |
| D5 first/resume | first 最多一次具体 Dispatch；resume 串行、任一步失败停止；拓扑序确定 |
| D6 TaskState | 先复用现有 task/attempt 投影，新状态采用 derived view/版本化字段，避免老值不兼容 |
| D7 真实 E2E | 至少两类真实依赖任务，多种措辞、真实 Qwen、实际 persisted MCP Receipt 验证，不能仅看模型文字或 HTTP 200 |

## 结论与门禁

- Step 2 的行为方向认可，但 Step 3 必须逐项消除 R1–R6，再授权 Step 4 实现。
- 本轮未修改程序、未执行用户本地 pytest/PostgreSQL；GitHub CI green 不代表正式多步骤 DAG 已实现。
- PR #35 继续 Draft，不合并；设计文档本身的通过不授予工具写入、并发执行或自动迁移许可。
