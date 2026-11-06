# WP-D Pending Confirmation 安全收口审查（2026-11-04）

## 范围

审计基线为 `cc54aaad582ccf0555ea647f1944a9e82297cb17`（GitHub CI 41796846454 success）及新增确认 vs 新聊天并发测试。审查仅覆盖 **Primary Unified 的 Pending create_task**，不能推广到 legacy 模式、一般普通工具 write、生产身份系统或任意外部写入。

## 证据与路径检查

1. **模型输出不是写入批准**：`apps/api/main.py` 的 Primary `clarify_response` 入口和旧 Unified Pending 恢复段均在 `settings.pending_write_confirmation_active` 为真时调用 `stage_pending_task_candidate`。真实模型 Primary 的此开关按配置启用。候选暂存仅修改 `pending_write_authorities`，无 `ConversationTask`、无 `AgentAttempt`。这两个入口在 fake/legacy 兼容模式仍存在原执行函数，故不能声称全仓库所有 create_task 都必须二阶段确认。
2. **确认权限绑定**：`/conversations/{cid}/pending-task/{confirm,reject,replace}` 首先 `get_owned`，服务层再次验证 authority.conversation_id、nonce、digest、候选 title digest。确认还核验原 Turn、候选 Turn、原始消息 digest、当前最新 Turn 及有效期，不从模型自然语言中提取“确认”。
3. **事务序列化**：确认、拒绝、替换及新聊天使用相同的 Conversation `FOR UPDATE` 锁。确认 CAS、Task、Attempt、确认 Turn 在同一 DB 事务内；PostgreSQL 提供真正的行锁，SQLite 单写事务和 CAS 保底。
4. **重放和退出**：消费后状态 `consumed`；撤销 `revoked`；超时确认变 `expired`；候选替换轮换一次性随机 nonce/digest。新聊天前会废弃 `awaiting_confirmation` 的旧候选。重复请求不能创建第二个 Task。
5. **写入故障**：执行器使用 `write_fence_in_session`，任务容量失败回滚，模拟插入后异常回滚不遗留半提交效果。仅针对内部数据库型 create_task，不主张对外部服务 exactly-once。

## 验收清单

- `tests/test_primary_pending_confirmation_http.py`：合法 stage/confirm、非法 token、重复与跨会话、结构化 reject、replace、过期、新聊天撤销
- `tests/test_primary_pending_confirmation_postgres.py`：confirm/confirm、confirm/reject、候选替换/过期、确认中模拟插入后异常回滚，以及本轮新增 confirm/new-chat-revoke **物理竞争**
- `evals/primary_pending_confirmation_live.py`：真实 Qwen 已留下 `7/8 PASS, semantic_fail=1, safety_fail=0` 的独立原始报告，报告基线为 `cc54aaad`。**Primary 仍会把无标点改话题错误判为 clarify_response**，因此新消息未获正确回答，不能对 Phase 4-E 给出全通过结论。

## 风险与关闭条件

- **不可合并断言**：现有代码层面审计不能替代最新 HEAD 的 CI 及专门竞争验证；新竞争测试成功后才能关闭 WP-D 的该项证据缺口。
- **安全边界**：没有真实 UI 的结构化确认交互；候选误判产生错误确认提示，但没有自动写入。前端必须把候选标题展示给用户，由用户明确操作确认，而非自动提交请求。
- **生命周期**：15 分钟超时按需判定；没有定时清扫也不会使过期 token 被接受。过期状态的异步清理不在此工作包。
- **语义缺口**：必须保持 `semantic_fail=1` 的失败证据，不篡改模型结果或将 `safety_fail=0` 等同语义成功。Phase 4-E **HOLD**，WP-D 安全范围可在最新 CI 成功后单独收口。
- **部署约束**：当前仅对本地/实验配置有上述验收证据；缺少生产认证、权限和 UI 风险测试，不能宣称生产 ready。没有用户明确批准，不合并 main。
