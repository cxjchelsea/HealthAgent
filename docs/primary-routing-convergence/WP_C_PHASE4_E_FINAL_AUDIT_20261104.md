# WP-C Phase 4-E — AC1–AC5 独立集中终审（2026-11-04）

## 裁决

**HOLD — 不得合并 main。** 本审查针对 `9915d5b50a7622eb9b565f327932de52520f4c3b`，冻结合同为 `ACCEPTANCE_CONTRACT.md` v1.2；与 `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f` 对比：ahead 248 / behind 0 / 87 个文件变化。GitHub Actions [41763215048](https://github.com/cxjchelsea/HealthAgent/actions/runs/41763215048) 已完成 success。当前分支尚无收口 PR。该 CI 主要使用 stub / fake 的自动化语义测试，不能取代 Qwen3:14b 真实语义验收。

## 五项冻结合同逐项审查

| 合同 | 裁决 | 新证据与限制 |
| --- | --- | --- |
| AC1 唯一 Primary 主语义 | **有力证据 / 尚未最终通过** | `PrimaryRouteEvidence` 现在以 `turn_id` 为主键持久化 proposal / schema / route / attempts / preturn digest / disposition / reason，已修复 11/03 审查报告原先的 P0；`tests/test_primary_durable_admission_http.py` 验证副作用前入库、拒绝/异常持久化、owner scope，`tests/test_primary_route_evidence_postgres.py` 验证并发唯一和进程硬退出保留。实际 Qwen 固定场景测得每轮 primary 1 次，但本 HEAD 的全路由真实调用计数与新独立验证仍未完成。 |
| AC2 十三项 Legacy Reviewer 零调用 | **PARTIAL PASS / 总体 HOLD** | `tests/test_primary_ac2_all_reviewers_http.py` 在 real-provider 配置（模型提案 stub）为十三项建立 fail-on-call；`tests/test_primary_wp_b_ac2_ac4_matrix.py` 覆盖 8 类非副作用/拒绝组合；`tests/test_primary_wp_b_static_callgraph.py` 对 Primary 分支做 AST 直接调用防护。AST 自述不能证明全部间接调用；尚未获得全状态真实模型函数级零调用审计与未见挑战。 |
| AC3 集中 Host Admission / 只能收窄 | **有力证据 / 尚未最终通过** | `agent/primary_host_admission_v1.py` 与 `record_primary_admission` 持久化 proposal→disposition；deny/restrict、旧授权、错误来源、跨 Turn/重放专项均有证据。未证明所有新异常交错和未来扩展工具均满足，仅按已覆盖范围裁决。 |
| AC4 统一副作用与执行证据 | **已定义工作包 PASS / 总体 HOLD** | 4-A 真实 Qwen+SQLite 回读 4/4；4-B 新版 revoked 回读 4/4（HEAD 738dc472）；4-C 隔离 PG 5/5 + 7/7（HEAD 738dc472）、revoke/consume 两序 3/3（HEAD dd6e733）；4-D 诊断 16/16、恢复 7/7（更新 HEAD 至 9915d5 前后），相关精确 CI 已通过。区分运行数量与独立用例。事务围栏、prepared unknown、M4 dispatch quarantine 均有证据；不承诺外部非幂等 exactly-once。 |
| AC5 兼容、真实模型与未见挑战 | **HOLD（阻断合并）** | Qwen Phase 3 固定业务矩阵 14/14（HEAD 5224ae2）；独立挑战 v3 12/12（HEAD 8499342），但从 v2 起已变为参与整改的已知回归集；4-B 实际 Qwen SQLite 4/4（HEAD 738dc472）。上述报告不在本审查 HEAD `9915d5b` 上完整重跑，且未见过的全范围挑战及成功率/失败分类仍不足。不能以 CI 模型 stub 冒充 Qwen。 |

## 对已完成工作的合理结论

- WP-C Phase 4-A/B/C/D 各自按各自已执行合同 **PASS**；运行环境/HEAD 不同，应如实记录，不能归并为同一次完整 Qwen + PostgreSQL 测试。
- 旧报告 `AC1_AC5_INDEPENDENT_ACCEPTANCE_20261103.md` 在更早 HEAD 的 durable evidence 缺失结论已过时；不可复制旧结论或删除历史 FAIL。
- `main` 保持 `41d91a3`，不合并；先执行以下最小收口门禁。

## 最小剩余验收（不再重构）

1. **AC5 最终真实 Qwen 兼容性回归**：在 `9915d5b` 或更新、固定的新隔离 DB 上重新运行 `evals.primary_wp_c_phase3_live` 全组（含 14 项）和 `evals.primary_wp_c_phase4_a_physical`（4 项），验证真实 Primary、撤销 revoked 与持久行绑定，均保存精确 HEAD。不能沿用已存在数据库或上一 HEAD。
2. **此前未用于 Prompt/实现修复的挑战集**：在测试前冻结样本与不变量，覆盖 CREATE/FIRST/RESUME/REVISE/CANCEL，ordinary calculate/MCP、create_task/save_memory 的允许/拒绝，pending 续答/撤销/改话题，未知计划、schema invalid、同 request_id replay 和权限变体；记录 PASS/FAIL/BLOCKED、模型与入口函数分组计量、错误分类，不允许对同一集调参后再称“未见样本”。
3. **AC2 调用图 + 函数级审计总结**：精确列出十三个函数从 Unified 当前入口无法取得路由权限；自动 fail-on-call 已有，但应在终审补齐 route-kind × state × negative-case 的覆盖表和实际运行差异，不把 AST 直接调用检查看作间接调用完全证明。
4. **CI/PR 合并门禁**：剩余验证收口后，用当时最新 HEAD 再核对 CI/分支保护/PR reviews/mergeability；只有用户明确 `AUTHORIZE MERGE #N` 才执行 Standard Merge Commit。

## 本次不建议的工作

不因本轮缺少 AC5 真实模型证据而重新设计 Host、AgentLoop 或原写入执行器；也不因为现有测试已通过就宣称整个项目生产就绪。
