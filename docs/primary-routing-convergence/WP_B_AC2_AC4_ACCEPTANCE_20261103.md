# WP-B — AC2 / AC4 集中执行边界复审（2026-11-03）

## 精确证据

- 仓库分支：`cxjchelsea/HealthAgent@refactor/primary-routing-convergence`
- 本次代码审查与 CI HEAD：`e06c36188055cfd6aa551981043eb548eaf29367`
- GitHub Actions：`41587522884`，**1068 passed / 3 skipped**；PostgreSQL 16 专项、迁移、代码检查以及其他 CI 步骤为 success。
- 自动化环境：GitHub Actions Python 3.12 + PostgreSQL 16。**并非**实际 Qwen3:14b + Ollama Proxy 的语义或 Windows 本地验证。

## 本轮修复

**F-WPB-01 — respond_only 仍能提前读长期记忆并触发强制召回（已修复）**

`agent/loop.py` 在常规工具暴露收窄之前具有“记住/还记得”快捷记忆检索入口和 `memory_recall_required` 重试逻辑。原来 `execution_mode=respond_only` 可能调用 `memory_store.list`，或在不允许工具的情况下要求调用 `recall_memory`，最终返回不适当的 `memory_recall_unverified`。现两个路径均明确排除 respond_only；`read_only` 对已授权记忆的查询仍保留。`tests/test_primary_ac4_respond_only_memory_fence.py` 完成正反回归。

## AC2 调用图与路由状态矩阵

- `tests/test_primary_wp_b_ac2_ac4_matrix.py`：真实 HTTP Turn + durable Primary/Host evidence；auto/explicit real-provider Unified × 8 类路由/失败状态：`plan_missing`、`reject`、`clarify`、`respond`、`ordinary_tool/read_only`、`save_memory/consent denied`、非法 schema、来源 quote 未绑定，另外包含 auto/explicit 双 Host restrict。
- 对冻结名单 13 个 Legacy Reviewer、M5 旧提案、二次执行策略、`UnderstandingService` 使用 fail-on-call 探针。验证没有旧语义回退、拒绝仍为零工具/零任务/零 Attempt，保存的 `route_kind` 和 Host disposition 与执行相符，重复 request_id 返回 409。
- `tests/test_primary_wp_b_static_callgraph.py`：对 `apps/api/main.py` 的 Primary 分支 AST 做静态扫描，阻止该分支直接调用 13 个 Legacy Reviewer、旧 M5、独立 Understanding/执行策略。该验证检查具体名字和代码边界，不等同于跨模块全仓库间接调用图证明。
- 既有 `tests/test_primary_ac2_all_reviewers_http.py`、`tests/test_primary_unified_dual_profile_lifecycle.py` 等继续覆盖 CREATE/FIRST/RESUME/REVISE/CANCEL、写入与 pending MCP 成功路径，防止用“大面积拒绝”换取零调用。

## AC4 Pending 未知执行结果

- `tests/test_primary_wp_b_pending_unknown_fence.py`：真实 `AttemptEvidenceStore` 先记录 MCP `prepared`，模拟调用工具后异常；下一次进入 Pending 查询时命中 `primary_v1_pending_reconciliation_required`，没有第二次分派。来源未验证时无 Attempt 且零工具调用。
- 前期 AC4 PostgreSQL 双入口写入锁、Pending 单次消费、崩溃/重放、M4 计划 CAS/恢复的独立专项在上述 CI 中继续通过。

## 判定

- **本轮代码及自动化覆盖 PASS**：在所列场景内，Legacy 十三项零调用、Host 收窄、Respond-only 记忆隔离、Pending MCP 未知结果不自动重派发通过。
- **AC2 / AC4 完整验收仍 HOLD**：函数名 AST + HTTP 模型桩并不能证明所有跨模块间接调用不存在；未对所有外部 MCP 故障、AgentLoop 多步组合、跨工具长期运行及物理大并发做穷举。Fake-provider explicit Unified 测试夹具仍保留旧兼容路径（不得称作新 Unified 全面接线）。
- **AC5 NOT RUN**：尚无本 HEAD 的 Qwen3:14b 实际语义、成功率、时延与挑战集证据。
- **main 合并仍 HOLD**。下一工作应进入可重复的独立真实模型物理验收，并以失败的真实案例决定是否还有代码缺口；不能通过放宽冻结 AC1–AC5 合同宣称通过。
