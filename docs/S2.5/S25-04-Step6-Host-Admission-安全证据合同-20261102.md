# S25-04 Step 6 — 前置分流准入证据合同与安全决策（2026-11-02）

## 实测问题
真实 Unified HTTP 普通计算 (19+23)*4 在 Trace-only 关闭/开启时均得到 partial、m5_unified_create_refused、m5_create_invalid_step_count；M5 在普通 AgentLoop 前提出无效 CREATE，且一旦绑定拒绝就必须拒绝，不允许后置回退。独立分类 Shadow 输出 ordinary_tool，但 Host 审查为 independent_exclusion_missing。

## 结论：目前不能安全启用前置分流
当前 Shadow label/quote 只能证明模型说了什么、引文是否属于本轮文本，**不能证明请求不包含计划控制或混合意图**。将同一模型另一轮判断当作独立确认不是独立证据。仅凭 unique quote、checkpoint 存在、模型信心、单项成功率（87/90）均不得跳过 M5。

## Host admission 的最小可信合同
- Turn 身份：conversation_id、turn_id、client_request_id，与创建 Turn 时的可信捕获绑定；重放和并发冲突必须失败关闭。
- Pre-turn plan：同一 Turn 创建事务内捕获的原始快照及校验状态；checkpoint 读取失败不可当作“不存在计划”。
- Current-turn evidence：唯一逐字 quote 与请求全文及偏移匹配，不使用模型自报 offset 作为权限。
- 操作域：ordinary_tool / plan_control / ambiguous 仅作为非权威建议；plan_control 由 M5 继续所有，ambiguous 不能绕过。
- **独立排除证据**：必须证明整个请求的执行计划意图和混合意图都已排除，且证明来源独立于该分类模型。目前尚无这样的 Host verifier；默认拒绝；不得以硬编码词表、关键字扫描、另一次自报 label 或布尔值默认 true 冒充证明。
- 执行授权：即使未来分流普通请求，也仍由 Host 现有 execution_policy、工具参数源绑定、写同意、幂等与审计独立校验。

## 下一步实施顺序
1. 只读独立审计：集中列出 ordinary、plan_create、plan_missing、FIRST、RESUME、REVISE、CANCEL、混合请求及含恶意工具 Observation 的正负场景，并包含计划快照过期、用户取消、重放、并发、失败的边界。
2. 设计真正可验证的 Host 排除机制（例如可信宿主意图授权来源，或明确范围的操作专用入口）；如果无法从自由文本可靠证明“不含计划控制”，则不能做自动安全分流，应考虑收窄为明确的用户选择入口。
3. 在任何改变 apps/api/main.py 前进行独立复审与测试，确保 M5 负向用例仍成立。
4. 后续再执行 Unified HTTP 真实模型物理验收；原 PR #29 保持 Draft，未经授权不合并。

## 防止回归的当前技术决议
本轮只新增决策文档与可复核的 negative tests，不修改 M5 提案的拒绝行为，也不将 AdmissionShadow.review_candidate 当作真实准入。
