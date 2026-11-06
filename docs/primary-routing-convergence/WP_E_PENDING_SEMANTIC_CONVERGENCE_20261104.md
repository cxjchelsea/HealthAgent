# WP-E — Primary Pending 当前轮语义收敛（2026-11-04）

## 既有证据与问题边界

- `phase4-e-pending-contrast-v1.json` 在 `02c55fa9`：11/13，通过 11；`semantic_fail=2` 且 `safety_fail=2`，两条无标点新请求被错误创建为任务。
- 新的结构化显式确认协议下，`pending-confirmation-live-v1.json` 与 `pending-confirmation-live-v2.json`（`cc54aaad`）：均 7/8，`semantic_fail=1`，`safety_fail=0`。真实 Qwen 仍把“换话题解释问题”误判为 `clarify_response`，但只生成候选、不直接写入。
- WP-D 于 `462c7c18` 的最新 CI 已通过（1172 passed, 3 skipped；PostgreSQL 新协议 5/5）。**不在本工作包修改这些写入授权合同**。
- 本轮只修 Prompt/上下文中的 Pending 语义偏置，仍然使用 **一个** Primary 模型调用，无关键词识别、无第二分类器、无 Host 强行改写路由。语义错误必须单独报告。

## 发现的提示结构问题

`agent/primary_route_v1.py` 过去的系统提示包含约 11 种路由的大型全局 Schema 说明，且末尾放置完整 `clarify_response` JSON 例子；其输出展示容易在验证过的 Pending 语境中强化“回复都是缺失参数”的默认解读。虽然先前追加过语义对照提示，但与当前 Turn 输入之间仍隔着多个输出示例。**这是结构上可能影响模型的因素，而非已经由模型消融实验证明的唯一根因**。

## 这次整改

1. 删除末尾独立的完整 `clarify_response` JSON 格式示例（原 Schema 和 RouteSignal 验证器保持不变）。
2. 增加 `_PENDING_CURRENT_TURN_SYSTEM`：明确当前用户目标优先；Pending 只是上文背景；“要求模型现在回答或执行的新任务”不同于“提供未来任务标题”；包括无标点场景、反事实判定、真正模糊时保守澄清。示例只用于语义差异说明，不作关键词程序分支。
3. `propose_primary_route` 在 Host 确认 `pending_origin_verified` 且 `pending_action` 属于已支持的 Pending 操作时，把语义优先规则作为第二条 system 消息，紧接在当前 user 结构化请求之前。一次 `provider.complete`，绝不新增模型或隐式修复其路由。
4. `tests/test_primary_pending_semantic_contract.py`：验证确有 Pending 时模型收到了优先说明、Schema 没有预选 route、仍只一次调用；无 Pending 时保持原路由消息结构。
5. `evals/primary_pending_semantic_unseen_v2.py`：**独立未见**的真实 Qwen 压力对照集，5 组 origin/reply + 一次显式确认 + preflight 共 12 项；分别计语义和写入安全，不能用安全通过遮盖误路由。新例句不得放入提示词作为few-shot示例。

## 验收门槛

- 精确分支 HEAD 的 GitHub CI 全部成功；无 Pendings 的路由测试不能被破坏。
- 在全新、空的本地 SQLite 上运行 `python -m evals.primary_pending_confirmation_live`（历史复验）与 `python -m evals.primary_pending_semantic_unseen_v2`（独立挑战），每个脚本使用单独数据库、单独报告，保留全部原始 FAIL。
- 主语义必须两个报告 `semantic_fail=0` 且安全 `safety_fail=0`，并核对真实 Qwen 单次 Primary 模型调用与 HTTP+SQL 物理记录。否则继续 HOLD，不把提示词变动当成验收成功。
- 不得更改真实 Qwen 测试的预期 route、调低阈值、改写先前报告，或用“模型误路由也没有写库”替代用户得到了正确回答的证明。

## 本地操作提醒

Windows PowerShell 在 `D:\HealthAgent`，更新 `refactor/primary-routing-convergence`；模型环境使用已验证的 `ollama_proxy / qwen3:14b` 与 `ROUTING_PROFILE=unified`，`PENDING_WRITE_CONFIRMATION_ENABLED=true`。两个真实模型脚本分别使用 `artifacts/wp-e/semantic-old-v3.sqlite` 与 `artifacts/wp-e/semantic-unseen-v2.sqlite`，并按各脚本结果归档报告。单次 API 启动只能绑定一个 DB，必须在两轮之间停止 API、换环境变量并重启。不得直接复用已经有 Turn 的数据库。

## 当前状态

仅完成实现和 deterministic tests 的代码提交；直到新的独立真实模型证据到位，**WP-E 与 Phase 4-E 均 HOLD**，不合并 main。
