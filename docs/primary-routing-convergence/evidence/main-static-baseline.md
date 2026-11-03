# HealthAgent main 静态调用点基线（只读）

基线：`main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`。来源：GitHub 读取 `apps/api/main.py`；此表仅统计该文件内形如 `函数名(` 的**静态调用位置**，不代表某回合执行了几次模型调用。没有运行线上或本地真实请求；运行时调用次数、耗时仍为待采集证据。

| AC2 明确禁用函数 | 静态调用位置（行号） | 数量 |
|---|---|---:|
| review_plan_cancellation | 396 | 1 |
| propose_first_route | 460 | 1 |
| review_plan_intent | 481、495、789 | 3 |
| check_plan_edit_intent | 531 | 1 |
| classify_pending_reply | 600 | 1 |
| review_pending_reference_lookup | 621 | 1 |
| review_unsupported_capability | 640 | 1 |
| review_read_only_clarification | 653 | 1 |
| reconcile_execution_policy | 744 | 1 |
| review_plan_creation_reference | 804 | 1 |
| review_missing_plan_reference | 819 | 1 |
| review_plan_execution_authorization | 884、969 | 2 |
| review_plan_execution_scope | 908 | 1 |

**合计：13 个函数名、16 个静态调用位置。**

其他需收敛的相关调用：`propose_new_plan_route` 339；`propose_saved_plan_route` 371；`review_plan_revision` 515；`decide_execution_policy_detail` 738；`UnderstandingService` 构造调用 577（另 130 行是其他定义/构造位置，不能算本轮调用）。第 1048 行为 AgentLoop。第 1269 与 1308 行分别是事后影子对照。

## 后续运行时计数方法和门禁

- 在未修改的 main 环境中，按原调用函数的**身份/角色**安装只读探针；仅记录调用类别、次数、耗时、route/admission 结论，不记录用户消息、模型内容和记忆值；以相同基线请求组合执行。
- 将 Host 主语义调用与 AgentLoop 选工具、事后 Shadow 分开计数；不将 AgentLoop 的合法多轮决策计为 AC1 违规。
- 记录拒绝、模型不可用和未进入业务执行的回合，避免通过“全部拒绝”降低调用次数。
- **目前没有运行时样本；不能声称 AC2 的现状计数已测得。** 此文档只是实施前可复核的静态位置清单。