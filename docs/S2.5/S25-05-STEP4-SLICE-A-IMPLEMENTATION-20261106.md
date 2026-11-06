# S25-05 Step 4 — Slice A 实现与验收边界（2026-11-06）

**状态：IMPLEMENTED IN FEATURE BRANCH / CI AND USER REVIEW GATES APPLY。** 本文档不等于全部 S25-05 Step 4 已完成；它仅记录第一条获授权的真实 HTTP 普通只读扩展纵向切片。

基线：`main@b2a17593ec062b030933e25ca7a35a1f6025701b`；分支 `feat/s25-05-step4-tool-registry-slice-20261106`。

## 1. 已实现内容

- `agent/tool_registry.py`：受信本地只读定义 `ReadToolDefinition`、`TrustedReadRegistry`、Pydantic 动态 Provider Schema、版本校验、允许集合/曝光集合二次检查、严格参数校验、稳定错误类别和可信 Adapter；目前仅注册示例工具 `text_stats`。
- `agent/config.py`：`S25_05_TEXT_STATS_ENABLED` 默认 **false**，即默认不更改现有生产/回归行为。
- `apps/api/main.py`：只在 Primary 已提出 `ordinary_tool + read_only` 且 Host `allow/read_only` 时，根据服务端旗标下发 `host_read_grants`。任何模型或客户端请求体不能直接设置此值。
- `agent/loop.py`：在普通只读 AgentLoop 的 Provider Schema 与执行路径加入已授予权限的 `text_stats`；执行时再次核验已授予/已曝光/版本/参数；要求待统计的文本确实出现在当前请求中，杜绝模型自行编造统计原文；显式 calculator-only 请求、`respond_only` 及 `semantic_topk` 过渡策略不扩大曝光。
- `services/evidence_store.py`：只将 `characters/words` 数值统计持久化为 Attempt 的 bounded projection，不保存待统计原文。
- `tests/test_s25_05_registry_slice.py`：Registry 拒绝、重复注册、Host 许可、AgentLoop 模型调用、完整 HTTP+真正 Attempt 持久化+重放冲突、编造参数拒绝。

统计口径：`characters` 为 Python Unicode code points `len(text)`；`words` 为按空白分隔的 `len(text.split())`，**不是中文语言分词器，也不代表医学语义抽取**。

## 2. 不变的边界

1. 没有新顶层语义分类器。Primary 和 Host 仍按 PR #30 的结构运行。
2. `text_stats` **仅**为普通只读工具；不会进入现有 `PrimaryRouteV1.ProposedStep/BoundStep`、保存计划执行/恢复或 Pending 写入。
3. 现有 `calculate/utc_time/list_tasks/recall_memory/mcp_reference_lookup` 仍保留旧注册/执行兼容机制；这不是“所有工具已经统一到 Registry”。
4. 没有给 Registry 授予 `create_task/save_memory` 等任何写入权限，也没有实现新的审批。
5. 尚未实现多 MCP 服务发现、全量 ToolInvoker 迁移、旧 Checkpoint 的工具版本迁移和真正的能力市场；这些属于 Step 4 后续切片，不能从 Slice A 的 PASS 推定完成。
6. 本轮模拟 Provider 只用于确定性验证。需要用户本机 `ollama_proxy/qwen3:14b` 的真实模型任务验证后，才能宣称该工具的真实模型可用性/成功率。

## 3. 本地真实 Qwen 验收前准备

在隔离开发环境 `.env` 设置：
```env
MODEL_PROVIDER=ollama_proxy
ROUTING_PROFILE=unified
S25_05_TEXT_STATS_ENABLED=true
ROUTE_SHADOW_ENABLED=false
```
并检查 `UNDERSTANDING_STRATEGY=direct` 等旧路由旗标与 `ROUTING_PROFILE=unified` 的冲突；切勿在未审阅的公网环境直接开放演示身份。保持模型连接地址和密钥在本地，不提交 `.env`。

推荐真实任务示例：`请统计这段文字的字符数和空白分词数：Hello world`，期待工具 Observation 的 `characters=11`、`words=2`。模型是否实际选择调用工具必须从 Trace 和数据库 Attempt 查证，不能只看最终自然语言回答。

## 4. 本轮实施通过的定义与未完成门禁

- 已建立受信只读 Tool Registry 第一纵向实现、默认关闭的 Feature Gate 和具备可核实的 HTTP/Attempt 回归用例。
- 仅在**本分支精确 HEAD 全量 CI SUCCESS** 才能标记“Slice A CI PASS”，且 CI 不等价于真实模型物理验收。
- 仍需做：实际 Qwen 多措辞验证、持久版本/迁移策略、旧工具渐进接线、M4 保存计划/恢复适配、MCP Catalog 受控映射、后续完整 Step 4 收口。
- **不得未经用户授权合并 PR，也不得宣布整个 S25-05 已完成。**
