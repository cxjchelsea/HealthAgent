# WP-C Phase 4-A 独立数据库物理验收（2026-11-04）

## 精确证据与裁决

用户在本机运行并上传 `phase4-a-physical-v1.json`；精确被测 HEAD `915ff403736979afe28010dba4f5e94901b2f966`，报告 timestamp `2026-11-04T02:22:04.119668+00:00`，`schema_version=wp-c-phase4-a-physical-v1`，`scope=synthetic_disposable_sqlite_readonly_audit`。**预检与三组业务物理 DB 回读均 PASS（4/4）**。执行驱动基于 localhost 的 Qwen3:14b/Ollama Proxy Unified Primary，独立 SQLite `mode=ro` 回读与 HTTP 结果交叉核验。

| 业务组 | Turn / PrimaryEvidence | PendingWriteAuthority | Task | Attempt（工具及状态） | 独立回读 |
| --- | --- | --- | --- | --- | --- |
| task | 2 / 2 | 1 条 `consumed` | 1 | 1 `create_task/succeeded` | PASS |
| cancel | 2 / 2 | **1 条 `pending`** | 0 | 0 | PASS |
| mcp | 2 / 2 | 0 | 0 | 1 `mcp_reference_lookup/succeeded` | PASS |

每组 `evidence_ok=true`、`effect_ok=true`、`http_ok=true`。报告证明了所测数据在数据库中的数量、Primary 证据一一绑定、授权正常消费、撤销零副作用、MCP 零写入。报告未提供原始数据库副本、逐行时间戳、崩溃/并发证据；不将上述模拟场景外推到生产环境或 PostgreSQL。

## Phase 4-B 发现：撤销后授权仍为 pending

`services/primary_pending_write.py` 的 `pending_write_authority_for_origin` 只接受最后一个 `partial needs_input:create_task` Turn，因此正常 HTTP 后续请求已不再使用旧授权。但 `consume_pending_task_write_v1` 单独校验的却是原始 Turn 仍为 `partial` 且 `PendingWriteAuthority.status=pending`，**没有验证原授权是否已被随后独立 Turn 终止或取代**。因此低层执行器若仍持有合法 origin/authority ID，可能对已经撤销却仍 pending 的原授权发起新的写入消费。这是当前必须独立处理的残留权限生命周期缺口。

建议不弱化 Phase4-A 历史 PASS，也不将它解释成“授权已吊销”；下一步设计并测试在撤销 Turn 原子终结时 durable CAS 将原授权改为 `revoked`，保证低层写执行器按原有 `status=pending` 判据拒绝。必须保留正常短答 Task 创建时的 consumed 流程，并证明单次授权、失败回滚、重放和并发不能被扩大权限。后续新 HEAD 的 Phase4-A 评分器需将撤销状态预期由 `pending` 更新为 `revoked`，但旧报告维持原样存档。

**裁决：Phase4-A 既有验收合同 PASS；Phase4-B 授权生命周期 HOLD；Phase4-C/D/AC4/AC5 尚未完成，不可合并 main。**
