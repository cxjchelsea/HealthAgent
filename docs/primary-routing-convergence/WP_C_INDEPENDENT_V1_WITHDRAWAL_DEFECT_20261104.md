# WP-C 独立挑战 v1 发现与 Pending 写入撤销围栏（2026-11-04）

## 真实证据及裁决

本机 Qwen3:14b/ollama_proxy/Unified/Primary v1 测试：`independent-challenges-v1.json`，精确代码 HEAD `ff6c0d3a499340c6d75c3f69d58d390f38737178`。冻结 11 个业务请求 + preflight，共 **10 PASS、1 FAIL、1 BLOCKED**。

- 新说法的 Task Pending 3/3 PASS，MCP Pending 3/3 PASS，记忆无同意拒绝 PASS，缺失计划阻断 PASS。
- **P0 缺陷**：`TASK_CANCEL_REPLY` 当前输入“算了，这个待办不创建了。”，模型输出 `clarify_response`；Host 给出 `primary_v1_pending_task_authorized`，运行时消费原始写权限，实际执行 `create_task` 并返回 `pending_task_created`。尽管重放与原始授权有效，但本轮用户已经撤销了执行意愿。
- 同组下一次独立问答 `TASK_CANCEL_FOLLOWUP` 因前项失败 BLOCKED，不能当作已通过。

## 根因

`RouteSignal` 的来源 quote、Schema 和 trusted Pending provenance 校验均不能证明当前短答确实是“任务名称”。`consume_pending_task_write_v1` 仅用 `reply.strip()` 作为 title；`user_supplied_value` 验证的是字面来源，不是肯定创建意图。在唯一 Primary 模型对撤销语义误判为 `clarify_response` 时，原始持久权限被不正确消费。**不能用原先 Phase 3 14/14 PASS 覆盖本次真实的错误副作用**。

## 修复与设计边界

新增 `agent/pending_write_withdrawal_guard.py`：窄范围、仅拒绝的显式撤销语法安全围栏，用于保护已验证 Pending 写入；它不判断主意图、不确定路由、不推断标题、不授予任何权限。限制性检查有两道：

1. `agent/primary_host_admission_v1.py` 在 `clarify_response` + 已验证 `create_task` Pending 场景中读取**当前轮原文**，如明确撤销，则 `restrict/primary_v1_pending_task_withdrawn`，绝不进入 writer；`route_kind` 不变，不做第二次语义模型调用。
2. `services/primary_pending_write.py` 在消费一次性授权、数据库写入前独立执行相同的拒绝检查，防止直接调用 writer 绕过 Host 主入口。

新增包含模型**故意误判** `clarify_response` 的完整 HTTP 测试：证明原始授权虽然存在，撤销时没有 Task，也没有写入执行；后续独立请求不被原来的待办捕获。其他 tests 覆盖合法短标题、典型明确取消表达和未认证 Pending 不能生成权限。

**特别风险说明**：窄范围撤销语法屏障不是通用语义理解模块。无法证明所有口语、委婉撤销、反讽、混合语义或英文变体都能被安全屏障识别；也不能把关键词屏障改写成第二个主路由分类器。此修复覆盖已经证明的 P0，以及部分结构明确的等价撤销表达。更强的安全等级需要为一切高风险 Pending 写入增加单独且明确的用户确认机制，或使用具备可独立验证的写入确认协议；那会改变目前“短答标题立即创建”的产品合同，需要单独设计审查，而不是在本次修复中偷偷放宽验收。

### 待执行门禁

- CI 完整通过及精确 HEAD 绑定。
- 同一真实 Qwen3:14b 新代码重新执行**原独立挑战集**，本批已成为“定向回归集”，不得再叫作陌生挑战测试；原来 10 PASS 的合法请求不得回归为全拒绝。
- 针对撤销语义另建此前未用于修复的挑战集，测试正负表达、委婉撤销、非取消标题、改话题和竞争状态，先冻结判定再执行。
- Phase 4 PostgreSQL 独立回读：`ConversationTask`、`PendingWriteAuthority`、`AgentAttempt` 和 `Turn` 必须确认撤销后 0 task/0 succeeded write receipt；检查后续重复/不同 request_id 的权限阻断；不能仅依赖 HTTP trace。
- AC4、AC5 继续 **HOLD**；不得未经用户授权合并 main。

对应冻结评分工具：`evals/primary_wp_c_independent_challenges.py`，并不修改已有通过标准。
