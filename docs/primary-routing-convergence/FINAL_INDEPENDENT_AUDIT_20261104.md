# HealthAgent Primary Routing Convergence — 最终独立审查（2026-11-04）

## 裁决（截至精确 HEAD）

**整体 AC1–AC5：HOLD；WP-D 写入安全与 WP-E Pending 语义为已验证子范围 PASS。不得合并 main。**

- 分支：`refactor/primary-routing-convergence`；精确 HEAD：`8b5c58bd91240052f904252ac174ab2fc4a0b5e6`。
- 同 HEAD GitHub Actions `41804919075`：**success**。CI 主 pytest **1174 passed / 3 skipped**；PostgreSQL Pending confirmation **5/5 passed**。CI 并非本机 Qwen 真实模型证据。
- `main` 为 `41d91a3ae3fceeaa6f5401313f7471285b8cd86f`；GitHub compare `main...branch` 显示 **ahead=333、behind=0**，变化涉及大约百个文件（以 GitHub compare 结果为准）。当前未找到与该分支对应的 open PR；因此不能核对 PR mergeability、required checks、review/branch protection 的合并门禁。
- 本机 Qwen 原始对话附件：`semantic-unseen-v2.json`，**12/12、semantic_fail=0、safety_fail=0**；`semantic-historical-v3.json`，**8/8、semantic_fail=0、safety_fail=0**。两份报告中的 `head` 均为本次审查精确 HEAD；真实模型为 `ollama_proxy/qwen3:14b`，路由与物理任务读取按脚本检查。原始附件未声称已经归档到仓库。
- 这 20 个是**检查点**，不是 20 个独立随机语言样本或 100% 泛化准确率。上述两脚本都集中于 Pending，未覆盖 AC5 的全部业务维度。

## 冻结 AC1–AC5 判定

| 合同 | 证据 | 审查结论 |
| --- | --- | --- |
| AC1 主语义唯一所有者 | `propose_primary_route` 当前单一 `provider.complete`；Primary route evidence 在工具副作用前持久化；Pending 两组真实报告测得单一 Primary，代码和 pytest 回归通过 | **PASS（已覆盖路径）/ 全部状态组合仍需终审矩阵** |
| AC2 13 个 Legacy Reviewer 零调用 | `tests/test_primary_ac2_all_reviewers_http.py` fail-on-call（模型被 stub）；`tests/test_primary_wp_b_static_callgraph.py` AST 直接调用检测；Unified 主要分支错误不 fallback；当前 CI pass | **PASS（测试路径）/ 未证明所有间接路径及所有实模状态组合** |
| AC3 唯一 Host 准入、不可放大 | `admit_primary_route_v1` 输出确定性 disposition；DB evidence、拒绝、复用和来源测试；`admit_v1_canary` 处理未执行的计划控制 | **PASS（已测范围）/ 仍需完整 route×state 差异回读** |
| AC4 副作用一致性 | Pending 实模 20/20 检查点、DB Task/Attempt；WP-D Postgres 确认 vs 确认/拒绝/新聊天、过期与异常回滚全部通过；普通写入、MCP 与保存计划分别有早期物理证据 | **PASS（WP-D 安全子范围）/ 当前 HEAD 全类别组合不能由 Pending 报告代替** |
| AC5 兼容性与独立真实模型 | Pending 历史 8/8、新挑战 12/12，均精确 HEAD；Phase 3 原业务 14/14 为旧 HEAD `5224ae28`，Phase 4 等原始报告分散在旧 HEAD；没有当前 HEAD 的新协议全业务真实 Qwen 验收 | **HOLD（合并阻断）** |

## 明确检查的代码边界

- `apps/api/main.py` Primary Unified 路径：先 `propose_primary_route`、`admit_primary_route_v1`、`record_primary_admission`，后走 Pending stage、read-only MCP、保存计划、respond-only、普通写入等受控分支。异常保存拒绝，不进入 Legacy 修正。
- `agent/primary_route_v1.py` 仅在 Host 验证 pending origin 的情况下增加邻接用户输入的 Pending 语义 system 指令，保持一轮 `provider.complete`，Schema 不强制 `clarify_response`。
- `services/primary_pending_confirmation.py` 先 stage 不写 Task；确认单独绑定 owner-scoped API、nonce/digest、期限与原始 Turn，DB 同事务提交 Task + Attempt + consumed；candidate replace 轮换 nonce，新聊天作废旧 candidate。
- `agent/primary_ordinary_write_v1.py` 非 Pending 的显式普通写入保留独立证据执行器。**两阶段确认仅约束 Pending create_task，不能宣称所有 create_task 都需要确认。**
- `agent/config.py`：真实模型 Unified Primary 默认进入两阶段协议，legacy/fake 测试兼容路径仍存在。不要把“真实模型且无参数覆盖的自动路由”与任意部署配置一概而论。
- `tests/test_primary_ac2_all_reviewers_http.py` 只证明被测试的示例上没有调用 13 个退役 Reviewer；`tests/test_primary_wp_b_static_callgraph.py` 静态 AST 并不能证明任意间接模型调用不存在。

## 合并阻断项（非新增已证实安全事故）

### B1 — AC5 当前 HEAD 的跨领域全业务实模验收缺失

需覆盖 CREATE/FIRST/RESUME/REVISE/CANCEL、ordinary calculate、MCP 读、显式 create_task、save_memory 的允许/拒绝、pending MCP、pending task 结构化确认、取消及改话题、未知计划、重复 client_request_id、重放、无效提案与工具故障。要求逐项对 `route_kind`、Host disposition、Turn/Plan/Attempt/Task/Memory 实际数据库记录与调用计数核对，不以“全部拒绝无副作用”冒充兼容成功。

### B2 — 旧验收器的成功合同与新协议不兼容

`evals/primary_wp_c_phase3_live.py` 的 `PENDING_TASK_ANSWER` 在约 118 行仍要求 `termination_reason=pending_task_created`；`evals/primary_wp_c_phase4_a_physical.py` 与 `evals/primary_wp_c_phase4_e_unseen.py` 也按补标题即产生 1 Task/1 Attempt 来计分。当前正确行为已迁移为 `pending_write_confirmation_required` + `awaiting_confirmation` + 0 Task/Attempt，**只有独立结构化确认后**才 consumed + 1 Task/Attempt。须创建 versioned v2 综合评测或显式兼容模式，保留既有 v1 证据，不得篡改旧报告或修改旧断言以制造 PASS。

### B3 — 合并流程尚未建立

尚无该分支的 open PR，无法复核 PR 精确 SHA、分支保护要求、审阅和 mergeability。当前比较结果为 333 个提交；合并前需要独立 diff 审查并确认无非预期大范围改动，不能以最近 WP-D/E 通过替代整分支审计。只有用户明确提供合并授权后才能采用 Standard Merge Commit。

## 收口路线（最小化）

1. **先版本化全业务物理评测 v2**，冻结新协议状态机与旧能力的成功期望，再执行最新 HEAD 真实 Qwen；不要继续改语义提示词。
2. 如有新失败，按 route / admission / execution / evidence / provider 分层处理；任何整改产生新 HEAD 后应复跑相关矩阵及 CI。
3. 在 B1/B2 全部 PASS 后创建 PR 并复核精确 HEAD 的 CI、main 差异与保护规则；在得到用户明确合并授权前保留分支不合并。

## 本审查不支持的断言

- 不宣称整个 Phase 4-E 已 PASS 或可直接合并。
- 不把两份 Pending 实模报告说成全业务验收。
- 不声称语义泛化率为 100% 或 WP-D 是生产级身份授权。
- 不认为 CI 中 `3 skipped` 代表新增 PostgreSQL 竞争测试被跳过；该测试在独立 PostgreSQL CI 步骤确已通过。
