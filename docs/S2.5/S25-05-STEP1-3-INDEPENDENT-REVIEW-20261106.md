# S25-05 Step 1–3 新基线独立审查与实施前决议

日期：2026-11-06；基线 `main@d53e405a044c16eb53506724e4aa838865aa6956`。本文件用于审查 `S25-05-STEP1-3-REBASELINE-20261106.md`，并保留尚未解决的决定；不是已实施证明。

## 1. 审查方法

- 旧版设计原件：`docs/s25-05-capability-tool-governance-design-20261022` → `docs/S2.5/S25-05-能力扩展与工具治理-前三步设计.md`。
- 权威合并前置合同：`docs/S2.5/S25-05-PREFLIGHT-03-TOOL-INTEGRATION-CONTRACT.md`。
- 代码直接核查：`agent/tools.py`、`agent/skills.py`、`agent/loop.py`、`agent/primary_route_v1.py`、`agent/primary_host_admission_v1.py`、`agent/plan_intent.py`、`apps/api/main.py`、`agent/mcp_stdio_client.py`。
- 分析为静态接口审查，尚**没有**对新 Registry 执行任何模拟或真实模型测试；已有旧主链测试结论不能迁移为 S25-05 的实现验收。

## 2. 原设计保留/调整的审查结果

| 审查问题 | 结论 | 需实现阶段验证 |
| --- | --- | --- |
| 能力目录是否需要去掉多处工具声明？ | **需要**；`SCHEMAS`、`TOOL_SCHEMAS`、`execute_tool` 实际分离 | Pydantic→Provider JSON Schema 投影等价测试；兼容 Legacy/Fake |
| 是否允许 Registry 自行授予只读权限？ | **不允许**；必须新增明确可信 Host policy，默认拒绝 | `text_stats` 在未列入 policy 时 Adapter 零调用 |
| 新工具能否自动用于计划步骤？ | **不能**；Primary Literal、Host READ_TOOLS、AgentLoop 保存计划/恢复均固定 | 普通只读扩展与计划扩展分别追踪 |
| 能否让 Registry 统一所有写入？ | **不能直接做**；已有 Host 内部事务/nonce/Attempt 与写入围栏 | Legacy 与新模式下 write replay/consent 不变 |
| 是否需要另一个语义工具选择模型？ | **不需要**；选择只在经 Host 准入的目录子集进行 | AC1/AC2 不额外调旧路由；TopK 不增权 |
| 是否应该直接开放任意 MCP 服务？ | **不允许**；现有 stdio 受信模块和工具固定 | `tools/list` 与本地允许映射一致性、坏协议/注入测试 |
| 通用 Invoker 是否可跳过 M4 Journal？ | **不能**；保存计划/恢复存在专用 Guard+Dispatch 语义 | 原有 Crash/Replay/Plan CAS 测试全通过 |
| 是否本轮直接移除旧审查函数或关键词 Skills？ | **不需要**；不在 05 的主开发范围 | 隔离的 Legacy/Fake 回归与来源开关 |
| 是否本轮就加入审批/医疗工具/外部市场？ | **不需要**，留给后续独立授权 | 当前没有生产医疗能力凭据 |

## 3. Step 4 开工前的明确设计待审项（不能静默跳过）

1. **Host policy 表的实际接口**：当前 `PrimaryHostDecision` 只描述 route/effect/执行模式，没有工具名称的通用权限 Grant。必须明确由服务端绑定 `Turn/route_kind/scope` 的授权事实来源，不接受模型伪造的 `tool_allowlist`。
2. **日志/证据兼容**：普通工具中使用的 `AttemptEvidenceStore` 与 M4 Plan Dispatch 的凭据差异需分别验证；新的 `tool_version` / `catalog_digest` 先出现在可信执行上下文/Trace，是否持久化由真实恢复需求决定。
3. **Provider Schema 完整性**：由 Pydantic 自动生成的 `$defs`、`additionalProperties`、数值边界等需测试真实 Qwen Tool Calling。模型可见 Schema 可以做受限投影，但后端验证不可降低。
4. **计划扩展独立权限**：是否计划将 `text_stats` 纳入计划工具，须在首片后再决策；若纳入，需更新 Primary 结构化 Schema 和 Host/Plan Ledger/M4 的完整证据链，不能只改白名单。
5. **本机配置**：从 GitHub 无法核实用户本机 `.env`、密钥或代理是否可运行真实模型；执行 Step 5 真实模型验收前应使用已有前置配置审计的只读脚本验证实际 profile。
6. **测试数据与能力目录**：新增只读示例应该可脱离医疗数据验证，真实输入必须能计算预期输出，避免靠关键词命中成功率美化测试。

## 4. 交付门禁

- [x] main 精确 SHA 与 PR #31 合并后 CI 已在设计前确认。
- [x] 原 Step 1–3 设计已逐条对照新 Primary/Host 合同审查。
- [x] 已形成新版 Step 1–3 设计、双范围划分、默认拒绝、MCP 与版本机制及负例矩阵。
- [ ] 新设计分支通过精确 HEAD CI（仓库创建 PR 后核对）。
- [ ] 用户审阅并确认该版本，不自动将文档审查等同于 Step 4 授权。
- [ ] 正式实现分支从最新 main 新建；不直接从旧设计分支开发。
- [ ] 新工具 Host 许可、模型 Schema、执行恢复等细节在 Step 4 的第一个最小切片中有可复现单元/HTTP 测试。
- [ ] 正式实现前获得单独授权；文档 PR 若需合并也须明确授权。

**审查裁决：旧设计保留核心技术路线，已补充需要的“Host 显式许可”和“普通只读≠计划工具扩展”两条关键约束。设计可供用户审查，但还不能称为 READY TO IMPLEMENT。**
