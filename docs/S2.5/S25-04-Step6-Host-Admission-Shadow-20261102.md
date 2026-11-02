# S25-04 Step 6 — Host Admission Shadow 设计边界

Qwen3:14b 离线 Shadow 评测 87/90，原回归 60/60，新增 27/30。样本危险误分流 0 次不证明生产执行安全。

本次新增 agent/s25_04_admission_shadow.py 和测试。Host shadow 接收模型类别、请求原文、证据引文及可信 pre-turn checkpoint 状态；只有另有独立 Host 证据排除计划及混合意图，才可能成为 review_candidate。

## 不可突破的限制
- review_candidate 只是审查候选，不是放行或执行授权；execution_authorized 永远 false。
- mixed_or_plan_intent_excluded 目前没有 Host 自动证明来源，不能用同一模型的重复判断、关键词或模型自述代替。
- 不接入 apps/api/main.py 的执行决策路径；M5 拒绝不能后置回退到 AgentLoop。
- 当前 shadow_classify 只返回 label/reason，未向 Host 交付引文；正式接线前必须补可验证来源绑定。
- 真实写入同意、参数校验、checkpoint、并发和去重仍归原 Host 控制。

## 下一步关口
1. 增加非权威 API trace-only 观察，证实真实回复及持久副作用不变。
2. 定义独立可验证的 Host 排除证据和当前轮 quote 的绑定合同。
3. 在最新 HEAD 执行完整 CI、S25-02 负向回归及 Unified HTTP 真实物理验收。
4. 保持 PR #29 Draft，合并需要独立用户授权。
