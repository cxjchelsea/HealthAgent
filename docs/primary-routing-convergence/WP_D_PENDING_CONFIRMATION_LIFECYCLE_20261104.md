# WP-D — Pending Confirmation 生命周期及 PostgreSQL 物理竞争验收底稿

## 不改变的历史安全事实

在 `02c55fa9` 上，真实 Qwen 对 Pending 未标点新请求曾产生两次错误 `create_task`（11/13，通过 11；语义错 2、安全错 2）。两阶段确认后 `cfbf998b` 的真实 Qwen 报告为 7/8，通过 7，语义错 1、安全错 0；模型仍可能把改话题误判为 `clarify_response`。任何新协议检查都不得改写历史报告、声称主语义已经修好，或以安全围栏替代语义评分。

## 本工作包的确定性 Host 改动

- `0018_pending_expiry`：候选增加 `candidate_expires_at`，新候选默认 15 分钟；确认时用带时区的当前 UTC 值检查超时，超时后持久化状态 `expired`。不存在活动候选或过期令牌均不允许创建任务。
- 确认与结构化拒绝由同一会话父记录的 PostgreSQL `SELECT FOR UPDATE` 锁序列化。确认 CAS、单个内部 Task、AgentAttempt 和 confirmation Turn 必须在一个事务提交；异常时全回滚。
- 新聊天 Turn 开始时，在同一会话锁下撤销未确认候选，令牌不可再重放；客户端若要更改标题，必须使用专门的 `POST /conversations/{cid}/pending-task/replace`，提供旧 nonce/digest 和新标题。Host 轮换随机 nonce，重新计算 digest 与有效期，旧令牌失效，不产生 Task/Attempt。
- `POST /conversations/{cid}/pending-task/confirm` 和 `/reject` 继续依赖当前认证会话的 owner 身份；普通文字（包括“确认”）不是权限信号。

## 验收测试

- `tests/test_primary_pending_confirmation_http.py`：正常 stage/confirm 单次写入、重复与非法 token 拒绝、跨会话拒绝、候选 reject、候选 replace 与旧 token 拒绝、持久过期无任务、发新聊天后撤销候选。
- `tests/test_primary_pending_confirmation_postgres.py`：两个数据库 worker 同时确认同一 token；确认 vs 拒绝竞争；过期与替换后 token 失效；在 Task INSERT 之后故意抛异常验证完全回滚及原 token 安全恢复。CI 增加专用 PostgreSQL 作业步骤，不能只用默认 SQLite skip 代替此证据。
- 数据库 Alembic 0018 的 SQLite 新建库与 PostgreSQL 幂等重跑，以及旧版本数据保留，依赖全量 CI。

## 仍需谨慎的边界

候选过期为按需执行的持久拒绝：若没有任何后续请求，DB 可以短期留在 `awaiting_confirmation`，但用户携带过期 nonce 发起确认不能通过。此实现没有后台清扫，也未接入前端确认 UI；当前只提供 HTTP API 合同。

此工作包不承诺真实 Qwen 的语义误路由修复，亦不承诺对任意第三方外部工具 exactly-once；当前原子写入仅是内部数据库中的 create_task。

**验收门槛**：精确 HEAD CI success，包括新 PostgreSQL 竞争测试；然后在全新 SQLite 上进行确认、替换、过期、改话题的物理验收。全局 Phase 4-E 在独立语义合约与临床/工具风险审查前仍 HOLD，不合并 main。
