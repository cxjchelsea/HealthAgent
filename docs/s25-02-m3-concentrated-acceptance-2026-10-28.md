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
