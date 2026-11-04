# WP-C Phase 4-E final-head Qwen 真实失败与围栏整改（2026-11-04）

## 不得覆写的真实证据

原始报告 `phase4-e-final-head-live-v1.json` 对应 `8d068387133d79fa794deca9f1536e6d57b58d21`，9 个检查中 8 PASS / 1 FAIL。失败在 `switch_followup`：用户已切换话题并要求介绍模型上下文协议；Primary 错误输出 `clarify_response`，Host `allow`，返回 `pending_task_created`，DB `task_count=1`、`attempt_count=1`、`PendingWriteAuthority=consumed`。这是实质错误写入（非评测器问题），必须保持 FAIL 历史。其他正常续答、明确取消、缺失计划测试通过。

## 根因

`services/primary_pending_write.py` 原先将整条 `reply.strip()` 作为待办标题。原来的 `user_supplied_value` 判断只能证明字符串出现在当前消息中，不能证明当前消息是在回答上轮标题问题。模型的错误语义 `clarify_response` 因而被错误转换为 Host 的写入权限。

## 最小权限整改

在 `agent/pending_write_withdrawal_guard.py` 添加纯拒绝型 `standalone_pending_task_title` 形状围栏：标题必须是非空、最多 80 字符、不包含会话/句子分隔符、且未被显式撤销。这个检查是**必要非充分条件**：单句自然语言依然可能被误判为标题，因此它不是替代语义理解的关键词分类器，也不证明任意文本可靠区分任务名。

`agent/primary_host_admission_v1.py` 在 `clarify_response` 的 Pending `create_task` 准入时先运行形状围栏；不符合则 `restrict / primary_v1_pending_title_not_standalone`，不执行工具。

`services/primary_pending_write.py` 在直接执行器入口再次阻断同类请求，返回 `pending_write_title_not_standalone`，不可仅靠上游 Host 准入。

`apps/api/main.py` 在本轮 `restrict` 终结事务中将原 Pending 写授权 CAS 为 `revoked`。不修改模型的 `route_kind`，不把拒绝重新分类为 respond，也不尝试旧语义模块。

对应自动化新增：真实失败语句由确定性模拟 Primary `clarify_response` HTTP 回放，必须数据库 `revoked` 且零 Task/Attempt；直接执行器的无 DB 访问拒绝用例；正常短标题成功路径由已有完整 HTTP 测试保护。

## 安全及验收限制

- **预期兼容性取舍**：带逗号、多句式或问句的合法标题也会被保守拒绝，需要用户简化为单独标题；这是设计性 fail-closed，不应隐藏。
- **剩余风险**：短且无标点的无关命令仍可能被模型误判为标题；达到通用语义安全需更强的显式参数确认/授权绑定协议，而不是不断添加取消关键词。
- 原 `phase4-e-final-head-live-v1.json` 的旧评测合同同时要求改话题应识别为非 `clarify_response`。仅增加 Host 的安全围栏不等于模型语义分类通过；旧报告不能改写，后续相同评测可能仍 FAIL，但应看到 0 Task/Attempt + revoked。需分别报告**安全围栏**与**路由语义**结果。
- 这一轮改动后所有之前 HEAD 的 Qwen/CI 都不能作为新 HEAD 的最终物理证据；必须在全新 SQLite 上再跑回归且留存失败信息。
- 在新 HEAD 精确 CI + 本地定向回归 + 真实 Qwen 证据达标以前仍为 HOLD。不得合并 main。
