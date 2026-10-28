# S25-02 M3 集中验收（2026-10-28）

## 基线与不可变边界
- 分支：`feat/s25-02-m3-continue-from-main-20261027`；审查基线：`1297218d7e9c294f876ee9c3ade5b0c609d8a473`
- GitHub Actions run 40644140027：success，659 passed / 3 skipped。
- M3-C Shadow 是诊断路径，`readiness=HOLD`。不能据此接管生产路由。
- 本次仅汇总审查证据，不修改业务行为、不自动合并。

## 已观察到的真实 Qwen3 HTTP 证据（用户本地回报，非 CI 复现）
- 普通计算：`(19+23)*4` -> 168；calculate 一次；Shadow match。
- 两步计划创建：两个 pending；0 次工具执行。
- 已保存计划只执行第一步：第一步 completed，第二步 pending；calculate 一次；56088。
- 初期三种计划表达的 Shadow plan_create 分类正确但证据缺失；修复后单例证据恢复 `state=valid`、`route=plan_create`、`comparison=match`，`recovery_model_calls=1`，`diagnostic_model_calls=2`，耗时 4580ms；M3C admission 仍 deny，execution_authorized=false。

## 集中验收清单
1. 功能：单工具读取/计算；创建计划不执行；执行第一步/剩余步骤；计划修订；取消/无计划恢复；不支持工具和澄清。
2. 安全：没有写入/执行授权时不执行；计划 revision 和 CAS 不绕过；client_request_id 重放不重复产生副作用；并发不重复执行。
3. Shadow：对照器输出 MATCH/MISMATCH/UNAVAILABLE/COMPATIBLE_SCOPE_UNKNOWN 与原始 trace 对照；unavailable 和 compatible 不算严格一致；执行授权始终 false。
4. 证据：保存每条请求完整响应，核验终止原因、工具 trace、计划状态和诊断错误；观察二次模型提取所带来的延迟。
5. 结果：任何跨权限执行、重复副作用、状态错乱、无法解释的路由冲突必须 BLOCK；缺失证据或 scope unknown 单独列为诊断限制；保留 HOLD。

## 验收方式
- CI 证据已核验，但不能代替用户本机 Ollama Qwen3 真实测试。
- 真实模型回归一次性执行，不再按 D03-A/B/C 建立独立工作阶段；记录原始 JSON 和统一问题清单。
- 所有阻断缺陷集中修复后再跑一次完整回归，形成 PASS / PASS WITH LIMITATIONS / BLOCK 结论。
- 未经显式授权不合并分支、不打开 Shadow 生产决策权。


## 最终集中验收补录（2026-10-28）
- 前述审查基线与 CI 为当时历史快照；最终合并前以本节更新后的分支 HEAD、main、GitHub Actions 为准。
- 真实 Qwen3 计划生命周期：创建 `12*13` 与 `7*9`（两步 pending，工具 0）；第二步由 `7*9` 修订为 `8*9`（revision 2，工具 0）；仅执行第一步得到 156、第二步仍 pending；执行剩余第二步得到 72，第一步不重复执行。测试证据为用户本机 `m3-plan-lifecycle-final.json`。
- 保存计划禁止提前执行：UTF-8 原始请求“请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。”返回 `plan_created_pending_execution`，两步 pending、工具 0；Shadow `plan_create/match`。
- 取消已保存计划：独立会话建立两步 pending 后，请求“取消刚才保存的计划，不要执行其中的步骤。”返回 HTTP 200、`plan_cancelled`、`plan=[]`、工具 0；Shadow `plan_cancel/match`；Host preview `needs_evidence`，Shadow 不授予删除权限。对应用户本机 `m3-blocker-regression-utf8(2).json`。
- 前期失败请求曾因 PowerShell 向 `python -` 传输中文变成问号造成无效验收；有效结论只采用 UTF-8 修正版。此前取消 503 的 `step_limit` 已通过独立 Host 取消路径纠正。
- 自动化：GitHub Actions run `40654192818` 对提交 `6255f0a1b4d9e9d24a37103fbf2450b0b5cee517` 报告 SUCCESS；包含取消持久化检查及 Shadow 取消匹配测试。历史 run `40653551827` 亦 SUCCESS。
- 结论：M3 Shadow-only 集中验收 **PASS WITH LIMITATIONS**。限制包括：Shadow `m3c_admission` 始终拒绝真实授权、执行范围存在 `compatible_plan_execution`（不等于严格 match）、修订 Shadow 观察曾出现 `unavailable`。此结论不构成生产切换授权。
- 合并边界：仅允许通过受保护 PR 按仓库要求的标准合并提交并验证 main；不得启用 Shadow 生产执行。
