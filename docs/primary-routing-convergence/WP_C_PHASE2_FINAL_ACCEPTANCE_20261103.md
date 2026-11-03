# WP-C Phase 2 — 真实 Qwen3:14b 最终验收归档（2026-11-03）

## 裁决与来源

**Phase 2 PASS（9/9，0 FAIL，0 BLOCKED）**。来源是用户在本机执行 `python -m evals.primary_wp_c_phase2_live` 后上传的 `phase2-final.json`，运行时间 2026-11-03 08:55:11 UTC。该原始 JSON 由用户在对话中提供，此文档是可审计摘要，**不是**由 GitHub CI 重新运行真实 Ollama 的产物，也未把原始 JSON 冒称已提交。

- 仓库 `cxjchelsea/HealthAgent`，分支 `refactor/primary-routing-convergence`
- 被测试的精确代码 HEAD：`dcfa3878313ef15507a0c6e7fd42b561fd5a90af`
- 本机 API：`http://127.0.0.1:8012`
- Provider：`ollama_proxy`；Model：`qwen3:14b`
- 运行路由：`unified`；`primary_route_v1_active=true`
- 自动化回归：GitHub Actions run `41602604941`，1087 passed / 3 skipped（同精确 HEAD）
- 测试脚本：`evals/primary_wp_c_phase2_live.py`；输入为合成计划与算式，无真实用户健康信息

## 实测矩阵

| 场景 | 裁决 | 核验结果 |
| --- | --- | --- |
| preflight | PASS | ollama_proxy / qwen3:14b / unified / Primary v1 |
| CREATE_A | PASS | HTTP 200，`plan_created_pending_execution`；计划两个 pending，无工具调用；route_kind=plan_create |
| FIRST | PASS | HTTP 200，`pending_plan_steps`；completed/pending，仅 1 次成功 calculate；route_kind=plan_execute_first |
| RESUME | PASS | HTTP 200，`plan_execution_completed`；completed/completed，仅执行剩余 1 次成功 calculate；route_kind=plan_resume |
| REPLAY | PASS | HTTP 409；复用 RESUME request_id；0 模型 HTTP，0 工具调用 |
| CREATE_B | PASS | 独立会话；两个 pending，参数 `3*4` 与 `5*6`，0 工具调用 |
| REVISE | PASS | HTTP 200，`plan_revised_pending_execution`；保留第一步 `3*4`，第二步由 `5*6` 改为 `7*8`；pending/pending，0 工具调用；route_kind=plan_revise |
| CREATE_C | PASS | 独立会话；两个 pending，无工具调用 |
| CANCEL | PASS | HTTP 200，`plan_cancelled`；返回空计划，0 工具调用；route_kind=plan_cancel |

执行、修订与取消三组分别使用不同新会话。正常业务调用中报告的 `categories.primary=1`，`semantic_attempts=1`；诊断返回 `primary-route-v1` 提案和确定性 Host 准入证据，且 `disposition=allow`。以上是报告可证明的范围，`model_http_attempts` 在这些计划场景中为 1；不代表整个项目所有任务仅一次模型调用。

## 历史失败与修复边界

- 初版 Phase 2：8 PASS、1 FAIL，REVISE `plan_revision_conflict`。调整为从 Host 已保存快照提供只读计划参数，严格匹配原表达式；REVISE 定向复验通过。
- 完整 v2：6 PASS、1 FAIL、2 BLOCKED，FIRST `primary_route_steps_mismatch`。严格区分输入 `saved_plan_steps` 与输出 `steps`，非 CREATE 必须输出空步骤；执行组定向复验通过。
- 当前最终完整报告：9 PASS，0 FAIL，0 BLOCKED。**没有**通过放宽 Schema、自动修正模型错误输出、回退 Legacy 或提升 Host 权限获得通过。

## 未覆盖范围与下一验收门禁

本轮是受控示例的**业务链路真实模型物理验收**；不能据此证明：
- PostgreSQL 独立物理回读及跨进程重启后的持久性、各中间阶段 crash/replay/claim/journal/CAS;
- Pending MCP、Pending create_task、普通 write/save_memory 及 consent 负样本的真实模型路径；
- 并发写入/幂等跨入口、外部 MCP unknown outcome 与工具权限隔离；
- 未参与提示词修复的陌生挑战集合、重复抽样的语义稳定性、与原始 main 差异量化。

**后续：WP-C Phase 3 负责异常/权限/未知引用与 Pending 的真实模型负/正样本；Phase 4 负责 PostgreSQL/崩溃/并发/重放及独立数据库回读。最终 AC1–AC5 冻结合同仍需独立收口复审，现仍 HOLD，不得合并 main。**
