# WP-C 独立挑战 v2 — 已知撤销缺陷整改回归与最终回答质量核验（2026-11-04）

## 原始真实模型证据

用户上传的 `independent-challenges-v2.json`，运行代码 HEAD `397a425e29d0a4a56bcb1afa614139468f0fd158`，`ollama_proxy/qwen3:14b`、unified，**11 PASS / 1 FAIL / 0 BLOCKED**。

新表述合法 Task Pending（澄清/续答写入/replay）及 MCP Pending（澄清/续答读/replay）共 6 项 PASS。此前 P0 复现的 `TASK_CANCEL_REPLY` 已 PASS：`route_kind=respond`，`disposition=allow`，`status=completed`，`reason=model_final`，`tool_events=[]`，没有触发 `create_task`。记忆未同意阻断与缺失计划限制继续 PASS。

唯一 FAIL `TASK_CANCEL_FOLLOWUP`：HTTP 200、completed/model_final、respond/allow、仅一次 Primary 路由调用、无工具调用。但既有评分器还要求回答中出现**ASCII `56`**，上传的分享报告未存回答原文，因此无法从该文件证明结果回答确实错误；也不能反过来确认它说了中文“`五十六`”。

## 评分器缺陷整改（不得追认原始 FAIL 为 PASS）

评分器原来使用 `"56" in answer`，对“七乘八等于五十六”误判为 FAIL。新增 `correct_followup_math_answer`，同时允许数值 `56` 和等值中文数字 `五十六`，但不允许 `156`、`五十七` 或显式否定该值；保留路由 respond/allow、completed、0 工具、唯一 Primary 的原始要求。新增参数化单元测试。分享报告为该测试增加 `answer_check=correct|not_confirmed`，不存原始回答全文。**修订评分器不改变原始 v2 报告 verdict，必须新一轮真实 Qwen 复验才能确认。**

## 重要未闭环风险

本轮明示撤销在新代码上未误写，这是针对已知缺陷的有限回归。独立挑战 v1 因实际 FAIL 已变为整改回归样本，不再是从未用于调参的泛化集。Host 明确撤销限制使用狭义模式：不覆盖所有委婉撤销、隐式拒绝、矛盾标题以及跨轮改话题；尤其不能把模糊自然语言场景的安全保障等同于结构化用户确认合同。

若调整后仍为 `TASK_CANCEL_FOLLOWUP` FAIL，需通过本机隔离测试对响应正文进行人工核对，不能直接删除答案正确性断言、硬改结果或者把通用拒绝视为成功。后续在新样本上做撤销/标题边界压力测试与 Phase 4 物理数据库、崩溃及并发证据核验。冻结 AC4/AC5 合并门禁仍 HOLD。
