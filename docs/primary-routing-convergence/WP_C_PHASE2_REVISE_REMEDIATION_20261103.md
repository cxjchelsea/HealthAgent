# WP-C Phase 2 真实 Qwen 验收与 REVISE 定向整改（2026-11-03）

## 真实物理结果（修复前）

本机 Ollama Proxy / qwen3:14b，`ROUTING_PROFILE=unified`，报告 `phase2-live.json`，HEAD `c16d201fb1db57bc7739d056fad8e6af7e746d11`。总计：**8 PASS、1 FAIL、0 BLOCKED**。

- CREATE_A、FIRST、RESUME、REPLAY：通过。FIRST 后状态 completed/pending；RESUME 后 completed/completed；REPLAY 409、0 次模型 HTTP。
- CREATE_B：通过，两个 pending 步骤的存储参数为 `3*4` 与 `5*6`。
- **REVISE：失败**，HTTP 200, partial, `plan_revision_conflict`。Primary 提案 route_kind=plan_revise，Host disposition=allow，primary 模型调用一次，执行器未修改计划，没有工具调用。
- CREATE_C、CANCEL：通过。

对照源码 `apply_plan_revision` 的静态合同和 `tests/test_s25_02_m5_revise_preservation.py`：同一中文句子若提案的参数精确为 `old=5*6`、`new=7*8` 则允许修改。因此**目前只证实模型提案已经通过路由和 Host 初始准入，但后续严格参数绑定不通过；旧报告不含原始提案参数，不能断言具体是哪一个字段被错误提取**。

## 改动

1. Primary 生成协议增加运算表达式边界示例：`计算 5*6` 不能作为 `revision_old_argument`；必须仅输出精确 `5*6`、`7*8`。
2. 在一次 Primary 模型调用的上下文中传入**Host 快照只读步骤摘要**：step_number、expected_tool、expected_arguments、status；不将该摘要视为授权。无已保存计划时不得携带步骤。
3. Host `admit_primary_route_v1` 在给予 revision candidate 之前验证提案旧参数与保存快照 `expected_arguments.expression` 完全一致，不进行隐式 trim 或重写。冲突明确返回 `primary_v1_revision_old_argument_mismatch`，工具及写入仍被禁止。
4. 新增单元和集成回归检查：只读上下文、错误旧参数拒绝、原计划快照不变、合法值仍可修订、单 Primary model call。

这些修改改善信息与安全围栏；**没有真实复跑前，不构成 Phase 2 PASS**。

## 定向本机复验

先停止之前的 8012 API。更新工作分支并确认 HEAD，使用之前同一组 Qwen/Proxy/Unified/Guarded/structured 环境变量重启 API，可使用独立 SQLite 数据库 `sqlite:///./artifacts/wp-c/live-phase2-revise.sqlite`。然后运行：

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
python -m evals.primary_wp_c_phase2_live --endpoint http://127.0.0.1:8012 --revision-only --output artifacts/wp-c/phase2-revise-v2.json --timeout 240
```

脚本使用新的 CREATE_B → REVISE 会话。两步均通过后，还应运行完整 Phase 2 脚本复验 FIRST/RESUME/REPLAY/CANCEL 的非回归表现。若仍失败，检查 `reason`：Host 拒绝说明旧参数错误；`plan_revision_conflict` 说明后续严格修订绑定仍未通过；勿擅自降低安全校验或标记为通过。

未经显式授权不得合并主分支；AC5 仍 HOLD。


## 完整 Phase 2 v2 非回归（同日）

物理报告 `phase2-live-v2.json`，HEAD `f0de30c419e25ab4b99ad089a32ae8ee383761f7`：6 PASS、1 FAIL、2 BLOCKED。CREATE_B → REVISE 与 CREATE_C → CANCEL 均通过；CREATE_A 成功；**FIRST** 返回 `primary_route_invalid_signal`，细分 `primary_route_steps_mismatch`，未执行工具。随后 RESUME 与 REPLAY 被安全阻断。此报告只证明非 `plan_create` 的输出步骤不符合合同，不包含原始模型步骤，不能断言究竟复制了哪些内容。

整改：系统 Prompt 现在明确将 `saved_plan_steps` 作为**只读 Host 输入快照**，`steps` 作为**仅用于创建新计划**的输出字段；FIRST/RESUME 等所有非 CREATE 路由必须输出 `steps=[]`。同时加入经过 `RouteSignal` 校验的 FIRST JSON 示例，及非 CREATE 回显步骤仍然严格拒绝、无第二语义重试的测试。Phase 2 验收脚本新增 `--execution-only` 以定位 CREATE_A→FIRST→RESUME→REPLAY。

修复后的运行命令：

```powershell
python -m evals.primary_wp_c_phase2_live --endpoint http://127.0.0.1:8012 --execution-only --output artifacts/wp-c/phase2-execution-v3.json --timeout 240
```

此定向运行通过后仍需重新运行完整 Phase 2 非回归；直到完整实测 PASS 之前继续 HOLD，且不得无授权合并。
