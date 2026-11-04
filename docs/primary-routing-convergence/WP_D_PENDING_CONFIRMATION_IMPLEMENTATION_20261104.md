# WP-D：Pending 写入显式确认协议 — 首轮实现与验收底稿

## 背景和裁决

真实 Qwen 的独立 Pending 对照于 `02c55fa99cd44949c4ab7affbbeaf2a0360abecf` 取得 11/13 PASS：2 条无标点新请求被误判为标题并写入数据库，`semantic_fail=2`、`safety_fail=2`。原报告不可覆盖。这证明仅靠语义分类与标题形态围栏不足以授权写入。

本次实施两阶段协议，不继续以关键词“取消”黑名单修补业务写入决策。**语义识别错仍应计作语义 FAIL；即使 Primary 错，缺少独立确认时也必须 0 Task / 0 Attempt**。

## 数据与状态机

`pending_write_authorities` 新增可空字段：`candidate_turn_id`（Host 当前轮）、`candidate_title`（原始候选）、`candidate_digest`（SHA256）、`confirmation_nonce`（独立随机一次性 token）。迁移 `0017_pending_write_confirmation`，使用 SQLite 兼容的 batch 和命名外键。

- `pending`：上一轮 Host 保存原请求来源授权，但尚无标题候选
- `awaiting_confirmation`：Primary `clarify_response` 已经被 Host 收缩校验并保存候选，无工具调用
- `consumed`：用户提交**结构化确认动作**，Host 校验 Owner / Conversation / Origin / Candidate digest / nonce / 最新 Turn / M4 write fence，CAS + Task + AgentAttempt + confirmation Turn 同事务
- `revoked`：旧式撤销或结构化拒绝候选；不能再消费

不接受在普通聊天中回复“确认”作为写入批准；客户端必须发起显式 `POST /conversations/{cid}/pending-task/confirm`，并原样回传候选响应中的 `authority_id`、`confirmation_nonce`、`candidate_digest`、新的 `client_request_id`。显式取消使用 `POST /conversations/{cid}/pending-task/reject`，入参相同。

## 接入范围

`agent/config.py` 的 `pending_write_confirmation_active` 对**真实模型 Primary**自动为真，fake provider 通过 `pending_write_confirmation_enabled=true` 单独测试/迁移；显式 legacy 对旧行为仍保留隔离的兼容路径。本次未把整个旧接口的合同静默改为两阶段。两个 Unified Pending 内部入口都选择 `stage_pending_task_candidate` 而非直接消费：Primary `clarify_response` 与旧 Unified 恢复段。

候选消息返回 `partial / pending_write_confirmation_required`，并包括候选标题、digest、随机 nonce。没有单独确认不能写入。

## 关键测试

`tests/test_primary_pending_confirmation_http.py`：原 Pending → 候选 stage 无副作用 → 错误 token 拒绝 → 正确 token 一次成功 → 重放拒绝 → 零重复任务、跨会话拒绝、模型错误续答只能生成候选、显式 reject 撤销候选并阻止后续 commit。

`evals/primary_pending_confirmation_live.py`：真实 Qwen 三组受控场景：正常候选+结构化确认、无标点改话题、明确取消。分别报告 `semantic_ok` 与 `safety_ok`；不能以安全 PASS 替代语义 PASS。

## 尚未宣布完成的部分

- 精确 HEAD CI 和真实 Qwen 新协议物理验证仍待验收
- PostgreSQL 双 worker 相同 token 的竞争与故障注入需要专门验收
- 长时间闲置候选的持久超时/过期策略尚未接入，必须纳入后续工作包
- 候选展示和确认按钮尚未接入实际前端，当前是 HTTP API 合同
- 旧测试中按“补标题后立即完成”的功能期望是旧协议；不得直接修改其真实历史证据去制造 PASS
- 初始 Phase 4-E 仍保持 HOLD；不创建合并许可或合并 main

## Windows 最小本地验证

```powershell
Set-Location D:\HealthAgent
git fetch origin
git switch refactor/primary-routing-convergence
git pull --ff-only origin refactor/primary-routing-convergence
.\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force artifacts\wp-d | Out-Null
python -m pytest -x -vv --tb=short `
    tests/test_primary_pending_confirmation_http.py `
    tests/test_primary_pending_cancel_http.py `
    tests/test_primary_pending_write_v1.py `
    2>&1 | Tee-Object artifacts/wp-d/pending-confirmation-regression-v1.log
if ($LASTEXITCODE -ne 0) { throw "Pending confirmation regression failed" }
```

真实模型测试必须在一个**新的、空的**本地 SQLite 实例上运行，指向该精确数据库路径；JSON 输出不得覆盖上一轮报告。运行指令应以这项定向测试、数据库迁移与精确 CI 先通过为前置条件。
