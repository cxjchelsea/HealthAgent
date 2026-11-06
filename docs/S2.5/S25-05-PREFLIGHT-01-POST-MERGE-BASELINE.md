# S25-05 前置收口 01 — PR #30 合并后开发基线

状态：**已核实的 Git 基线；仅文档归档**。日期：2026-11-06。

## 1. 主线权威事实

- 仓库：`cxjchelsea/HealthAgent`。
- `PR #30`：https://github.com/cxjchelsea/HealthAgent/pull/30 ，**Merged**，采用 Standard Merge Commit（既非 Squash，也非 Rebase）。
- `main` 合并提交：`cdde8ebb6c278dda752a744f75051159285ff65b`。两父提交：原 `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`，PR 分支 HEAD `97d1ce5c910784c4dfaaa7f7b4d18df46868badd`。
- Post-merge GitHub Actions：https://github.com/cxjchelsea/HealthAgent/actions/runs/42151199328 — `success`，pytest `1184 passed, 3 skipped`；独立 PostgreSQL Pending confirmation `5 passed`；CI 运行于合并提交 HEAD。
- 精确运行时 Qwen 实验：`ollama_proxy/qwen3:14b`，`ac1-ac5-business-v3.json`，**23/23 PASS**，所测代码 HEAD `575e747d69abf6e5af29df803e0a2338f34665a5`；后续到合并提交主要是归档文档，**不能伪称物理实验直接运行于 merge commit**。历史细节见 `docs/primary-routing-convergence/AC1_AC5_FINAL_PHYSICAL_ACCEPTANCE_20261106.md`。
- 历史 PR 尚未合并、旧 main SHA、`HOLD` 等文字是其创建时点的**原始审计快照**。不可篡改原报告；本文件与下面两份前置合同作为“最终状态”的新权威记录。

## 2. 本次前置任务与明确非目标

当前分支 `docs/s25-05-preflight-closure-20261106` 以 `main@cdde8ebb...` 创建，只准备 05 的实施前审计与设计接线。该分支不能先于用户授权并入 `main`。**不涉及** `agent/config.py`、`apps/api/main.py`、`agent/loop.py` 或 Primary 等运行时代码修改；不含数据库迁移或人工在本地改 `.env`。

- 本阶段完成：PR #30 合并事实归档、配置矩阵/本地审查办法、S25-05 的 Primary→Host→AgentLoop→Registry 接口冻结。
- 后续 S25-05：统一可信 Tool Registry、Schema、Adapter、曝光、执行版本与工具治理。
- 另行进行：Legacy 代码物理拆除或通用化 Pending MCP 提示。当前不以代码清理替代已通过的安全/执行证据。

## 3. 旧设计的状态与衔接方式

原 `S25-05 Step 1–3` 文档位于 **单独且尚未合入 main** 的设计分支：
`docs/s25-05-capability-tool-governance-design-20261022`，文件为 `docs/S2.5/S25-05-能力扩展与工具治理-前三步设计.md`。

旧设计的核心目标（可信注册+统一 Invoker+受限 MCP 适配）仍有价值，但它形成于 PR #30 收敛之前。**本前置收口不表示旧设计已通过审查或 Step 4 已获授权**，进入 05 时须针对新的 Primary Host 边界重新审查，参见 `S25-05-PREFLIGHT-03-TOOL-INTEGRATION-CONTRACT.md`。

## 4. 进入 S25-05 的判定

本工作包“准备完成”需要：
1. 三份前置合同都已入同一个 PR，范围仅文档；
2. 以精确 HEAD 运行的 GitHub CI SUCCESS，`main` 不被本工作包直接修改；
3. 需要本机才能核实的 `.env` **单独标记未核实**，提供安全的 PowerShell 自查流程；
4. S25-05 设计审查确认 **新增工具不会绕过 Host**、不会新增主语义分类器、Legacy 行为不被误当作默认 Unified；
5. 用户对前置收口 PR 是否合并、S25-05 Step 4 是否实施分别给出授权；不可将文档完成等同于实现完成。

## 5. 参考记录

- PR #30 主链验收：`docs/primary-routing-convergence/AC1_AC5_FINAL_PHYSICAL_ACCEPTANCE_20261106.md`
- PR #30 上线/回退：`docs/primary-routing-convergence/PR30_RELEASE_GATES_AND_ROLLBACK_20261106.md`
- 本轮前置配置：`S25-05-PREFLIGHT-02-CONFIG-AUDIT.md`
- 本轮工具接线：`S25-05-PREFLIGHT-03-TOOL-INTEGRATION-CONTRACT.md`
