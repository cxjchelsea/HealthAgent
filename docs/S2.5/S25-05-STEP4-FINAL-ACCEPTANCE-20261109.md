# S25-05 Step 4 — 集中技术验收与进入 Step 5 的门禁（2026-11-09）

## 验收范围

PR #33 `feat/s25-05-step4-tool-registry-slice-20261106`，对照 `main` 和《S25-05-PREFLIGHT-03-TOOL-INTEGRATION-CONTRACT.md》的 G1–G10。此报告区分 **静态代码核查、自动化 CI 证据、真实模型现场验收**，不把三者混同。PR 保持 Draft，未获合并授权。

## 技术核查与证据

| 边界 | 代码路径/自动化证据 | 裁决 |
| --- | --- | --- |
| G1/G2：未曝光、未获授权、已禁用、工具版本不符 | `agent/tool_registry.py` 双集合/版本/启用态检查；`tests/test_s25_05_registry_slice.py` | 已覆盖自动化 |
| G3：严格 Schema，不得先分发再校验 | `agent/plan_read_invoker.py` 在 `PlanDispatchJournal.begin` 之前做 Pydantic `model_validate` 与完整字段比对；`tests/test_s25_05_m4_invoker_migration_gate.py` 含非法字段、空参数等零调用负例 | **本轮发现并修复**；待最新 HEAD CI |
| G4/G6：普通只读不得扩权至写入、Pending | `approved_read_grants` 只接 Primary ordinary/read_only 且 feature opt-in；M4 只读专属工具名；既有 Pending PostgreSQL 与 AC4 测试 | 已覆盖自动化 |
| G5：受信 MCP 目录 | `StdioMCPClient.call_trusted_reference` 固定服务、初始化、`tools/list` 对照后调用；旧保存计划保持 Legacy MCP 行为，**未声称两者等价** | 局部已覆盖 |
| G7：失败、重试和未知结果 | M4 Guard→Journal begin→执行→Receipt→CAS；失败不得无 Journal 二次派发；PostgreSQL M4 真实子进程崩溃、双 Worker 及离线恢复测试 | 已覆盖自动化；不承诺外部 exactly-once |
| G8：计划版本漂移 | 新建计划 Host pin `legacy-plan-v1`；历史 `NULL` 不自动补写；版本不匹配/混合时拒绝；正常与离线恢复要求 exact nullable version | 已覆盖自动化 |
| G9：真实 Qwen | 自动化测试使用脚本化 Provider/Fake，不能证明 Qwen 路由成功率、真实 Tool Calling Schema 兼容率或线上延迟 | **未验收** |
| G10：Legacy/Unified 隔离 | 现有路由、审查及兼容测试；主路由不由 Registry 改判，新增只读功能默认关闭 | 已覆盖自动化；现场 profile 待确认 |

## M4 迁移裁决

- 已将实际保存计划执行链的工具调用提取至 `PlanReadInvoker`，保留原 Legacy Adapter；这是**兼容迁移**，并非把保存计划改为借用普通 `OrdinaryReadInvoker` 授权。
- Host 创建 Turn、校验归属与存档快照；Guard 获取 M4 原始快照 Claim；新 Invoker 在 Journal Begin 前校验参数/版本/Guard；随后工具调用、Receipt、Checkpoint CAS。无 Journal 的独立测试兼容路径不能代表生产 Host 已准入。
- 普通 Registry、M4、Pending 写工具仍有不同的授权与证据域。未来更换 M4 MCP/Registry Adapter 必须引入新计划版本，不能重标历史版本。
- 新增工具 `text_stats` **不**自动成为保存计划步骤，也未开放新写工具。

## 风险与明确限制

1. 本次静态审查发现并修复 **G3：M4 Invoker 参数最终校验发生过晚**；必须以修复提交的 **精确 HEAD CI 全通过**为完成依据。
2. 真实 `ollama_proxy/qwen3:14b` 测试依赖本机 Provider、网络/代理和实际凭据，GitHub Actions 不具备这组环境；不能把脚本化 HTTP 测试算作真实模型验收。
3. PR 包含大量历史渐进提交，原 PR 描述停留在 Slice A；合并前需要将 PR 描述更新为整个 Step 4 的范围和未完成门禁，避免误导审查者。
4. 当前 CI 仅运行选择性 Ruff/Mypy，并非全仓严格静态类型检查；结果不可扩大解释。
5. 无独立 GitHub Review 提交记录。此份核查是当前助手对代码/CI 的审计，不冒称第三方独立复审。

## 裁决口径

**Step 4 代码/CI 技术门禁**：以最新 HEAD 全量 pytest、PostgreSQL M4 并发/子进程崩溃/双 Worker、Alembic、既有评测通过为准。通过时可进入 **Step 5 的真实模型和人工端到端验收**，不再继续在 Step 4 无限增加微切片。

**Step 5 / 默认开启 / 合并 main**：仍需真实模型测试记录、独立风险签收及用户显式授权。未验收前 PR 保持 Draft，不合并、不默认启用 Registry 功能。
