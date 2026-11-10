# S25-06 Step 3 v2 — 审查整改追踪与二次复审门禁

- 日期：2026-11-10
- 来源：S25-06-STEP3-FEASIBILITY-REVIEW-20261110.md（F1–F6）
- 修订文件：S25-06-STEP3-TECHNICAL-DESIGN-20261110.md（v2）
- **状态：DESIGN ADDRESSED / SECOND REVIEW PENDING**。下述“已整改”仅指设计文本给出具体机制，不意味着实现/测试通过。

| 审查项 | 审查风险 | 修订 v2 对应位置 | 设计状态 | 二次复审必须检查 |
| --- | --- | --- | --- | --- |
| F1 P1 | 同 Turn 下游读取不到 checkpointed 前序结果 | §4 逐步骤 CAS、Claim snapshot 同事务滚动、当前 Guard 更新；§8 故障矩阵 | 设计已具体化 | 锁序和 CAS + Claim 同事务；终局不重复投影；崩溃后处理 |
| F2 P1 | text_stats 不属于 legacy 保存计划执行器 | §2 双版本、§5 新 plan-read Adapter/明确 Host grant、Primary 新提案 | 设计已具体化 | new adapter 的固定工具版本映射和权限矩阵，不提升 ordinary 授权 |
| F3 P1 | 动态引用模板无法通过现有具体参数比对 | §2.2 executions 实例记录、§2.3 区分模板/执行实例/授权；§4.2 新版比较器 | 设计已具体化 | 新旧 Dispatch/投影互不破坏；不猜测 step_id |
| F4 P1 | bounded Receipt 可能截断且不完整 | §6 只引用完整定义字符串，禁止回退/补造 | 设计已具体化 | 真正源字段范围、owner/revision、截断边界与目标 Schema |
| F5 P1 | 只在 loop 分流不能覆盖取消、修订、最终化 | §7 API 全生命周期矩阵，flag off/unknown fail closed | 设计已具体化 | 每个正式入口和早返回路径；历史 Plan 不被误读 |
| F6 P2 | 专项物理验收与成本约束 | §8 崩溃矩阵、§9 分阶段、§10 验收方案 | 待实现验证 | 真 Qwen、PostgreSQL 双 Worker、Crash/CI 和非过拟合评测 |

## 仍有实现前风险，不得直接视为通过

1. **锁与事务：** 需要确认 PostgreSQL 的真实 UPDATE 行锁顺序、同事务 Claim.snapshot 变更和 API Guard 的合法轮转；不能沿用旧 preturn_raw guard。
2. **回调与投影：** 当前 S25-03 project_finalized_turn 对同一 Turn 已存在 AgentAttempt 会拒绝；新增 per-step 投影必须具备真正的幂等插入及一致性冲突拒绝，同时终局不能再次全量写。
3. **版本命名：** `dependency-plan-read-v1` 是新计划执行协议，`1.0.0` 是具体工具 adapter 版本；二者不能混用，且不得借 Registry 开关给计划隐式授权。
4. **多步骤生成：** 尚未用真实 Qwen 证明模型可可靠输出新 Schema 及有效依赖边；Step 4 必须渐进开发，先 A/B 纯校验再 C/D 接线。
5. **Crash 语义：** 工具可能执行但 Receipt 丢失；不能承诺 exactly-once 或自动补偿。对处于 observed 但未 checkpointed 的步骤，保留人工 reconciliation。
6. **新格式取消：** 对有运行 Claim 的取消请求需遵循旧锁，不允许绕过正在执行的 Turn。恢复、只读展示、flag off 均需审查。

## 下一步与通过标准

- Step 3 v2 二次复审：逐条检查 F1–F5 是否已变成可实现的接口与事务不变量；任何 P1 未闭合则保持 Draft。
- 若设计通过，再授权 Step 4 Slice A：仅严格 Schema/DAG 和单元测试，不改默认主链。
- 生产功能实施前补本地物理测试；不要把文档 CI SUCCESS 说成 DAG 功能已验证。
- PR #35 仍待用户审查与显式合并授权。
