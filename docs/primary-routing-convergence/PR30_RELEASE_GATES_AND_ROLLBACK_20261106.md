# PR #30 — 合并门禁、路由发布与回退方案

日期：2026-11-06。范围：`refactor/primary-routing-convergence` → `main`。本文件只描述部署/审查操作，不授权合并，也不声称已完成生产环境演练。

## 冻结状态与证据

- PR: https://github.com/cxjchelsea/HealthAgent/pull/30 — **Draft / Open**。
- 实际代码及归档 HEAD `cac8e134bcd8fd8180edb720a6c1be6118a7600b`（后续文档提交仅改变 HEAD，不改变 Python 行为）。每次 PR 审查必须刷新 **精确** HEAD/CI。
- GitHub PR CI `42147061288` success；原始 Qwen `ollama_proxy/qwen3:14b` 23/23 物理矩阵绑定运行时 HEAD `575e747d69abf6e5af29df803e0a2338f34665a5`，详见 `AC1_AC5_FINAL_PHYSICAL_ACCEPTANCE_20261106.md`。
- 比较范围：355 commits / PR 117 changed files（合并前重新读 GitHub 实时结果）。
- GitHub 仓库 `allow_merge_commit=true`。`main` protection 接口返回 **HTTP 403**，无法读取 required checks / minimum review approvals；`rulesets` 返回空不等于无保护，不能据此判定门禁已满足。
- PR review 列表和 review threads 暂时均为空，requested reviewers 为零，PR 是 Draft。这不是已经获得独立批准的状态。

## 一、明确区分合并与上线

**合并代码**只改变 Git 历史。部署时选择哪条路由由 `Settings` 及环境变量决定；不能把 GitHub CI 通过当作真实线上启动行为已验证。

| 配置 | 运行时预期 | 用途 |
|---|---|---|
| `MODEL_PROVIDER=fake`, `ROUTING_PROFILE=auto` | 现有 fake/compatibility fixture，`primary_route_v1_active=false` | 测试、离线评测 |
| `MODEL_PROVIDER=ollama_proxy`, `ROUTING_PROFILE=auto` 且未显式设置旧 M4/M5 flags | 新默认 Unified，Primary 生效，Pending 写入需要结构化确认 | **显式接受发布风险后**的灰度 |
| `MODEL_PROVIDER=ollama_proxy`, `ROUTING_PROFILE=unified` | 强制 Unified，必须与 guarded/structured/M5 flags 一致 | 显式部署新协议 |
| `MODEL_PROVIDER=ollama_proxy`, `ROUTING_PROFILE=legacy` | Legacy/Direct，不走 Primary v1 | **短期回退**；仅适用于经过评估的旧环境 |
| `ROUTING_PROFILE=auto` 且显式设置部分路由 flags | compatibility/拒绝冲突组合，不保证走 Unified | 不推荐部署；须清理旧 flag |

**注意：**回退路由不会撤销已创建任务、记忆、计划、授权候选或数据迁移。不同路径的 Pending 确认/任务写入合同可能不同。执行回退前必须阻止新请求、核对尚未完成/待确认的请求，并明确告知客户端接口和用户行为变化；不得静默切换。

## 二、部署前必须满足

1. 使用真实认证网关/身份系统验证请求身份，**不能**使用 Demo 身份作为生产鉴权。至少设置 `ALLOW_DEMO_IDENTITY=false`，配置非空合法 `USER_TOKENS` 仅适用于受限开发/测试；内置令牌映射本身仍不是建议的生产身份系统。同步强制 HTTPS、密钥管理与访问隔离。
2. 在隔离环境备份实际 PostgreSQL/SQLite 数据库。执行 Alembic `0015_primary_pending_write` → `0016_primary_route_evidence` → `0017_pending_write_confirmation` → `0018_pending_expiry`，复查 schema、原有数据、数据库索引、外键与版本。CI 已通过空/合成数据库迁移及重复执行，**不能替代现有生产数据迁移演练**。
3. 明确预期 routing profile（推荐对发布系统**显式设置** `ROUTING_PROFILE`），禁止遗留 `ROUTE_ADMISSION_MODE`、`M5_*_ENABLED`、`UNDERSTANDING_STRATEGY` 等冲突环境变量意外覆盖发布决策。
4. 使用已通过认证的测试用户检查 `/model` 响应的 `routing_profile`（实际有效配置）、`routing_profile_source` 和 `primary_route_v1_active`；注意 `/model` **没有** `effective_routing_profile` 和 Pending 确认状态字段，后者用本地 `Settings.pending_write_confirmation_active` 单独核验。关闭 Demo 身份且未配置测试凭据时，`/model` 预期返回 401，不是路由故障。复查 `/healthz` 、`/readyz`。
5. 在真实模型的隔离测试用户/独立数据库上验证 CREATE/FIRST/RESUME/REVISE/CANCEL、read-only MCP、普通任务写入、记忆同意、Pending 标题暂存→结构化确认、撤销、重复请求 409 和断点恢复。禁止使用真实病历/医疗决策数据。
6. 记录观察窗口内模型拒绝/Provider 错误、工具超时、重复请求、`prepared`/unfinalized write、授权消费状态及人工回滚阈值；**不要**自动重试未知副作用。
7. 确定旧客户端对新两阶段 Pending 协议的兼容策略。历史 v1 评测的“补标题立即创建”不再是 Unified 的正确行为。

## 三、本地/隔离环境配置确认命令（Windows PowerShell）

以下命令只读取配置，不启动 API，不发业务请求，不合并代码，也不修改数据库。

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
$env:MODEL_PROVIDER = "ollama_proxy"
$env:ROUTING_PROFILE = "unified"
$env:ALLOW_DEMO_IDENTITY = "false"
# 在隔离部署环境使用真正安全的认证；本机无需在此脚本中粘贴任何真实密钥。
python -c "from agent.config import Settings; s=Settings(); print({'profile': s.effective_routing_profile, 'source': s.routing_profile_source, 'primary': s.primary_route_v1_active, 'pending_confirmation': s.pending_write_confirmation_active, 'demo_identity': s.allow_demo_identity})"
```

预期：`profile=unified`, `primary=True`, `pending_confirmation=True`, `demo_identity=False`。如存在旧环境变量与 profile 冲突，`Settings` 应拒绝启动；不得通过删除安全检查来“让服务跑起来”。

隔离环境回退准备（**只供运行时发布选择**）：先停流量、审查未决副作用和客户端协议，再在新的 API 进程中明确设置 `ROUTING_PROFILE=legacy` 并移除与之冲突的显式 `ROUTE_ADMISSION_MODE`、`UNDERSTANDING_STRATEGY`、`M5_UNIFIED_ROUTE_ENABLED`、`M5_FIRST_ROUTE_ENABLED` 等覆盖；复核 `/model` 的 `primary_route_v1_active=false`。这不是数据库回滚，且未在生产环境演练。

## 四、PR 的剩余门禁

- [x] 真实模型全业务回归及 PostgreSQL 物理边界的现有证据已归档。
- [x] 重点安全/事务/迁移代码已审查并形成 PR 评论；**不等于 117 文件逐行审查**。
- [x] GitHub 支持 Standard Merge Commit；目前 PR 在 GitHub 表示 `mergeable=true` / `clean`。
- [ ] 由具有仓库管理权限者在 GitHub Settings → Branches / Rulesets 实际确认 `main` required checks、审查人数、是否限制直接提交、是否要求 up-to-date branch 等（工具 403，**不得推断**）。
- [ ] 获得正式独立 code review，尤其是变化量 355+ commits / 117+ files 的未复核范围，确认 code ownership / 本地机密文件排除。
- [ ] 明确部署目标：只是开发分支合并，还是准备启用 Unified 到某个真实环境；记录发布及回退负责人。
- [ ] 更新到合并时精确 PR HEAD，再次核对 checks、review、`main` SHA 和 `mergeable_state`。
- [ ] 取得用户明确 `AUTHORIZE MERGE #30`，才允许尝试 Standard Merge Commit。不得用 Squash/Rebase 代替，不得绕过保护。
- [ ] 获批并合并后做 Post-Merge Verification；不得提前声称 main 已更新。

**当前结论：PR #30 保留 Draft / HOLD。**
