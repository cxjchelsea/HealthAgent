# S25-02 Step 5 D05-F01：计划修改与结构化理解冲突（2026-10-23）

## 真实问题
用户已创建两步计划（步骤 1 = MCP 查询 RAG、步骤 2 = calculate 8*8），然后输入“第一步不是计算 88，而是计算 99，其他步骤保持不变”。正式接口出现 `understanding_unavailable`（light: `missing_goal`，strong + retry: `clarification_status_mismatch`），尚未进入计划修改。该用户措辞本身又与现有步骤绑定矛盾，不能自动将“第一步”改指第二步。

## 修复范围
- 新增 `agent/plan_revision_review.py` 的受约束模型识别：当前回合是否是在要求修改已有计划，拟修改的步骤编号、计算工具、旧参数和新参数。无法结构化验证则保留普通流程，不扩大写权限或工具权限。
- 在 `apps/api/main.py` 对已有检查点的合法计划修改请求，先独立判定、按 PlanLedger.restore 验证，然后进入专用计划修改门禁，避免普通 Understanding 对计划编辑产生 `missing_goal` 与 `clarification_status_mismatch` 连续 503。计划请求的修改必须不执行工具。
- 仅限未完成的计算步骤，且步骤编号、旧表达式、新表达式都与**当前用户原文及持久化绑定**一致，才替换指定参数、增加计划 revision、保留其他步骤和原有 step id/evidence。对于显式“第一步”与真实步骤 1 MCP 查询矛盾，返回 `partial/plan_revision_conflict`，完整保留检查点，解释应澄清步骤编号，不假装修改成功。不将“88”未经确认转写为“8*8”。
- 不可靠模型输出或未经验证的修订不会更新计划；已完成步骤拒绝直接改写，不允许通过修订给工具增加未授权参数。当前修复**不是**任意领域的动态重规划，专注于用户已绑定的计算步骤编辑。
- 新增单元测试 `tests/test_s25_02_d05_plan_revision.py`、API 集成测试 `tests/test_s25_02_d05_revision_api.py`：矛盾第一步保留状态并澄清、明确修改第二步产生 `plan_revision=2`、旧步骤绑定不被修改、已完成步骤拒绝编辑、模型错误推断步骤编号拒绝。对既有 D03/C01–C05 保持完整回归。

## 人工复测
1. 新建会话，原样创建“第一步查询 MCP 中 RAG 的定义，第二步计算 8*8”，需两步 pending，无执行。
2. 输入原始冲突：“第一步不是计算 8*8，而是计算 9*9，其他步骤保持不变。”预期 HTTP 200、`partial/plan_revision_conflict`、`steps=0`、无工具调用、旧计划和 revision 不变；不是 HTTP 503，也不私自把第一步改成计算。
3. 明确输入：“请把第二步计算 8*8 修改为 9*9，其他步骤不变，不要执行。”预期 `plan_revised_pending_execution`、revision 增加 1，MCP 步骤仍 pending，计算步骤参数为 9*9，且本轮仍无工具执行。
4. 后续单独授权执行时，执行 9*9=81 而非 8*8=64。仅在观察到真实工具和状态证据后判 PASS。

自动化 CI 仅验证受控模型/接口行为；**真实 Qwen D05 仍需人工复测**。PR #22 Draft；不得合并 main。
