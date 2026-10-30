# S25-02 Final — Step 6 独立集中复审（2026-10-30）

**结论：条件通过，允许建立 Draft PR；不允许直接合并或宣称生产完全验收。**

## 精确证据

- 审核基线：main 8789c0178a0b4962969068e820b302bd4ca3ea59；HEAD 635c63e55913bbe81602d523664e15816716cb18。
- main 对比 feature：ahead 25 / behind 0，改动 14 个文件；主要生产修改为 agent/config.py、agent/m5_unified_route.py、apps/api/main.py。其余为测试、验收脚本、文档。
- 精确 HEAD Actions #40981630859：SUCCESS，包括全量 pytest、PostgreSQL 迁移与 M4 crash/row-lock/two-worker 检查、编译、Ruff、mypy、评估脚本。
- 上传的 live(1).json：run_id 47f3f51e080f，Qwen3:14b，auto_real_provider_default，guarded+structured+unified；11 PASS / 0 FAIL / 0 BLOCKED。
- 上传的 physical(1).json：SQLite 34 PASS / 0 FAIL；准确的两个 checkpointed Dispatch，无其他 plan dispatch，无残留 claim，计划修订、取消及 integrity_check 符合预期。
- 每个新可路由请求 primary=1、plan_intent=0；REPLAY 与 PENDING_ANSWER 不会重新进行主要计划路由。

## 风险登记及处置

| ID | 级别 | 事实 | 收口处理 |
|---|---|---|---|
| S6-01 | PASS | 默认 unified 已由真实模型 11 项 + SQLite 34 项报告支持 | 保留为限定范围内的 PASS |
| S6-02 | LOW | 本次响应出现 route_shadow，虽然代码默认 false，可能存在运行时显式开启；报告不足以证明确切来源 | 记录为可选诊断，核查部署开关 |
| S6-03 | LOW | REVISE 和 PENDING 的 shadow 诊断 invalid_shadow_route_combination（execution_scope / effect_intent），不影响业务结果 | 未解决诊断缺陷；不声称 Shadow 全通过 |
| S6-04 | MED | 未收到 git-head.txt 或运行进程 Git SHA，无法独立证明本机 API 就是审核 HEAD | 最终合并前补运行版本证据或明确降低证据断言 |
| S6-05 | MED | main branch protection 接口返回 HTTP 403 Resource not accessible by integration；GitHub rulesets 查询为空不等于可证明无保护 | 合并前核对 GitHub UI 的 required checks、保护及精确 mergeability |
| S6-06 | SCOPE | PostgreSQL CI crash/recovery 成功，但没有真实 Qwen3 + PostgreSQL 物理 E2E | 标为 NOT COVERED；不得宣称生产级 exactly-once |
| S6-07 | LOW | .env 中显式 UNDERSTANDING_STRATEGY 造成 auto compatibility 已由用户清理 | 保留迁移说明与生效来源检查 |

## 主链设计及安全判断

- 真实模型无路由覆盖时，auto 将 guarded/structured/M5 统一主链设为有效模式；fake 和显式兼容配置仍有回退。
- 无计划的 plan_missing 是由 M5 主判定生成的否决信号，仅在 Host 确认无 checkpoint 后返回 plan_not_found，不拥有执行授权。
- saved-plan other 不再通过旧 plan_intent 获得计划执行权限；待澄清来源不重新进行主要计划分类。
- M4 Host admission、CAS、claim、Dispatch Journal、recovery 数据写入链未在本 Diff 中改写。
- Shadow 属于 post-commit 诊断，不具有执行权限；其两处 Schema 错误不构成已证实的主链安全漏洞。

## Step 6 合并门禁

1. Draft PR 只能用于集中审查，不得自动合并。
2. PR HEAD 的全部 required CI / review / mergeability 重新核验；main 前进后重核对差异。
3. 本机实际服务的精确版本及两项 Shadow 诊断缺口必须诚实记录。
4. 用户明确发出 AUTHORIZE MERGE #N 后才允许 Standard Merge Commit，并开展 Post-Merge Verification。
5. 未经上述步骤不得标记 S25-02 CLOSED，也不得启动 M6/M7/M8。