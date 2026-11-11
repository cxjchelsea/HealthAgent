# S25-06 Step 6 — 最终完整性审计（2026-11-11）

**审计状态：开发限定范围具备收口条件；正式 CLOSED 待本审计 PR 审查、CI 与用户明确验收。**

## 1. 精确基线和审计方法

- 仓库：`cxjchelsea/HealthAgent`，`main@727a5f39747d36a1f36faa52bd8a6353b8a515fa`（PR #47 已合并，已核实远端 main identical）。
- 上位能力合同：`docs/S2.5-通用自主Agent能力补齐开发策略.md` 的 S25-06「多步骤规划与执行：任务分解、步骤依赖、执行顺序、状态转换」及各能力统一 Step 1–6 收口要求。
- 当前已认可的**实际限定实现合同**：`docs/S2.5/S25-06-STEP6-DEVELOPMENT-CLOSEOUT-20261111.md` 和 `docs/S2.5/S25-06-INTEGRATION-HANDOFF-20261111.md`。二者形成于 PR #44/#45 前后，为**历史范围声明**，并非 #47 后的完整最终验收。
- 审计依据：main 的配置、schema、Primary Host、依赖读取 Host、执行与恢复实现；PR #36–#47 合并历史；GitHub exact-PR-head CI；用户操作的两份真实 Qwen 正向/语义挑战报告。检查证据而不是用绿色 CI 推断模型能力。
- 用户本地提交的真实模型 JSON 为**会话外证据**，没有提交原始 prompt、密钥或模型输出到仓库；报告由用户上传并由审查者分析，不应当宣传为仓库保存的可复现运行记录。

## 2. 已实现的 S25-06 最小交付合同

| 目标行为 | 实现与主证据 | 状态与边界 |
| --- | --- | --- |
| 多步骤版本化计划创建 | `dependency-plan-v1` / `dependency-plan-read-v1`；严格初始 `pending`、拓扑 ID、2–8 步、工具版本；`agent/dependency_plan_schema.py` 和 Slice A 单测 | **完成（限定范围）**：只认可受信只读工具对，不是任意工具 DAG |
| 参数来源与可信依赖 | `mcp_reference_lookup@1.0.0(term)` → `text_stats@1.0.0(text=observation_ref 第一步 definition)`；`services/dependency_observation.py`、Slice B/C 测试 | **完成（限定范围）**：只允许已验证、已 checkpoint 的 `definition` |
| 单一语义提议 / Host 准入 | `agent/primary_route_v1.py`、`agent/primary_host_admission_v1.py`；E1/E2 回归 | **完成**：模型提议从不等于工具执行授权；旧计划不隐式迁移 |
| CREATE 原子落库、无副作用 | `apps/api/main.py` 终结事务的 CAS + SAVEPOINT；E2 HTTP/PG 测试；冲突响应不宣称保存成功 | **完成**：创建步骤不调用工具或产生 Claim/Dispatch |
| FIRST / RESUME、可信证据 | `agent/dependency_plan_host.py`、`agent/dependency_plan_runner.py`、`services/dependency_dispatch.py`、`services/dependency_execution.py` | **完成（顺序执行）**：FIRST 仅第一步，RESUME 逐个按已验证前驱执行，不承诺任意拓扑调度/并发 |
| 可信恢复 / 禁止未知重放 | Claim + generation Fence；PG 两连接锁冲突、真实子进程受控崩溃、迟到 Receipt/恢复先后次序测试（Step 5） | **完成（开发验证）**：未知结果不推断成功或自动 replay；无生产 operator IAM/worker-death attestation |
| CREATE 展示真实计划 | PR #47；展示已校验工具名、版本、字面值、observation 引用；HTTP 测试校验与 durable snapshot 同步 | **完成**：展示不构成用户确认协议，更不能证明语义等价 |
| 默认关闭与来源隔离 | `Settings.s25_06_dependency_plan_enabled=False`，真实 provider 启用要求非 demo 身份，Host 只读白名单 | **完成**：不是生产环境默认开关已开放 |
| 正式 API 真实模型闭环 | `evals/s25_06_e3_live.py`；用户本地 `ollama_proxy/qwen3:14b` 两轮 CREATE → FIRST → RESUME | **6/6 PASS**；合成身份/SQLite/受信 MCP，非生产拓扑 |
| M04 原始误替换问题 | PR #47 Primary 目标忠实性提示、取消伪语义正则、显示精确计划、真实 Primary 评估 `evals/s25_06_m04_fidelity_live.py`（改进后检查查询词及 observation 引用） | **已知原始场景 8/8 PASS**；不是任意语句的无误计划保证 |

## 3. 证据冻结

| 证据 | SHA / 结果 | 正确解读 |
| --- | --- | --- |
| 最后生产变更 PR #47 | 合并 `727a5f39747d36a1f36faa52bd8a6353b8a515fa` | 远端 main 已含功能 |
| PR #47 exact-head GitHub Actions | HEAD `9145669f42c359e76b66c01db431a9504722b296`，run `42982169876`，**1513 passed / 5 skipped / 1 xfailed**；PG jobs SUCCESS | 测试证明其自动化覆盖范围；`xfail` 是**未满足的语义防护期望**，不是 PASS |
| M04 真实模型 `s25-06-m04-fidelity-final-v2.json` | 用户上传，`ollama_proxy/qwen3:14b`，**8/8 PASS** | 四类 Primary-only 路由/绑定挑战各重复两次；不执行工具或写计划 |
| E3 真实模型 `s25-06-m04-e3-final.json` | 用户上传，`ollama_proxy/qwen3:14b`，**6/6 PASS** | 两个合成会话的 CREATE/FIRST/RESUME；第二步真正使用第一步已保存 Receipt 的 `definition` |
| PR #44 独立物理验证 | main 的 Step 5 审计文档和 PG 测试 | 真 PG 进程/事务/锁冲突；**不等于**真实 FastAPI 多 worker 生产服务重启 |
| Step 6 文档 PR #45 | 合并 `93c74eda1fbf68eaa1fb0cdfe4a332d596fb93e4` | 收口候选文档已经进入 main；文内基线 `ad09384` 和未勾选 checklist 是历史快照，不能当作 #47 后实时状态 |

## 4. 未完成、明确不属于本次开发限定范围的能力与风险

| ID | 项目 | 归属 / 处置 |
| --- | --- | --- |
| G-01 | **通用用户目标—计划语义等价**。结构合法但目标被替换时 Host 可能允许保存，仓库留有 **1 strict xfailed** | **真实缺口**，不得声称已经实现。交由后续跨能力目标忠实性合同与 S25-08 最终验证/S25-18 Benchmark 设计；若产品要求计划入库前必须证明忠实性，应另立阻塞门禁 |
| G-02 | 版本化依赖计划的 plan_cancel / plan_revise | 当前 Primary Host 明确返回 `dependency_control_not_supported`；**非开发限定合同内已实现**。计划局部重规划归 S25-07，任务控制/取消归 S25-16；执行时不得回退到 legacy |
| G-03 | 一般工具动态选择、任意拓扑 DAG、多工具/写操作、平行调度 | **当前 S25-06 限定工具集以外**；扩展需要同时修订 Schema、Host、Adapter、权限与 E2E，不得从 2–8 步数量推断工具无约束 |
| G-04 | 生产 operator IAM、worker-death 独立证明、服务级多 worker 灾难恢复、外部 exactly-once | **生产部署阻塞项**，不是开发试验已完成项；禁止借用测试 verifier 和开发 bearer 承诺生产授权 |
| G-05 | S3 医疗 Agentic RAG、医学证据与临床合规 | **下游阶段**，S25-06 不负责证明 |
| G-06 | 持久化后必须得到用户逐项确认才可 FIRST | 当前仅显示计划 + 必须用户另发明确 FIRST/RESUME 命令，**没有**单独逐项审批合同；后续人机协作与工具审批阶段评估 |
| G-07 | 历史设计 Draft PR #35 / #40 仍开着且部分设计内容已被实施超越 | **行政清理事项**。不要合并过时设计，也不要把它们当成未完成运行代码；由用户授权后关闭或归档 |

这些「不在本次限定范围」是**基于仓库 Step 6 合同的范围判断**。上位 S2.5 V2 策略仍规定最终 18 项能力集成验收，不能把 S25-06 限定版完成等同于 S2.5 或 S3 准备就绪。

## 5. 收口判定与下一动作

### 判定

- **S25-06 bounded development implementation: IMPLEMENTED / VERIFIED。**
- **可申请 CLOSED（dev-scope only）**：合并的 PR #36–#47、限定只读执行证据、Step 5 真 PostgreSQL 失败边界、E3 6/6、M04 8/8 与残余风险登记构成足够的限定版开发收口证据。
- **不得称 FULL GENERAL PLANNING 或 PRODUCTION READY**。如果将“计划自动保证语义忠实”视为 S25-06 必要入库不变量，则 G-01 必须继续开放，本次只能称限定版完成。
- 本审计**不启动功能开关、不更改任何运行时代码、不代替用户授权**。

### 正式 Step 6 退出事项

- [x] 原始 18 项策略与实际已批准的 S25-06 限定合同完成比对。
- [x] 代码、验收、物理恢复与历史 PR 证据归档。
- [x] 新/旧计划、默认 OFF、Host、Claim/Fence/Receipt 的职责边界确认。
- [x] `xfail`、取消/修订、生产 IAM、S3 接口等剩余项分类并指定后续归属。
- [ ] **本审计 PR 最终 exact-HEAD CI**、独立复审。
- [ ] **用户明确批准本审计 PR 合并**，合并后核实 main 并在项目进度表将 S25-06 标记为 `CLOSED (development-only, bounded read-only dependent planning)`。

### 下一工作项

正式开发范围收口后，再为 S25-07 Observation 驱动重规划单独审查最新 main、制定新设计/验收，不要把 S25-07 的失败重规划、S25-16 的取消控制或 S25-08 的结果交付偷换为 S25-06 已实现能力。
