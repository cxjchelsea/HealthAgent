# S25-02 Step 5 · C02 Understanding 503 脱敏诊断补齐

日期：2026-10-23。PR #22；不并入 main。

## 背景

真实 Qwen C02 输入“请使用计算工具计算 2**10。如果工具不支持…”在正式 Conversation API 收到 HTTP 503，`code=understanding_unavailable`、`decision_steps=0`。B03 的另一轮也曾出现相同代码；成功的 C01 和全新会话对照说明不能仅以终态判断是连接断开、解析或一致性问题。此刻**没有** C02 失败时模型原始尝试的证据，不能猜测根因。

## 实施

- 现有 `UnderstandingService.understand()` 已维护按模型角色的 `attempts`，本次不修改重试次数、模型提示与授权策略。
- `apps/api/main.py` 在该服务抛 `UnderstandingError("understanding_unavailable", attempts=...)` 时，从 attempts 构造**最多四条**脱敏记录，固定枚举 `role`、`outcome`、有限的 `retry`、`duration_ms` 与允许列举的 `diagnostic`。无原始模型输出、上下文、用户输入、参数、Prompt、密钥、异常堆栈，不记录未验证的任意字符串。
- 只在 HTTP 503 的该错误响应 `detail.understanding_attempts` 中返回；其它错误沿用原协议；已有 owner-scoped Turn 诊断和 `Turn.failure_code` 仍保留。不开放跨用户访问，也不把失败掩盖成 `completed`。
- 新增 `tests/test_s25_02_understanding_diagnostics.py`：注入 light/strong 的结构化失败及恶意敏感字段，确认 API 503 可定位但不会暴露原始字段；其它异常不多报 attempts。此测试为受控证据，不是 Ollama/Qwen 实际复现。

## 手工取证与判定

在 PR #22 更新分支、重启后，在**独立新会话**重复原 C02 输入。如果再次 503：浏览器 DevTools → Network → `messages` → Response → `detail.understanding_attempts`，记录各角色 `outcome/diagnostic` 与 HTTP 耗时，并提供启动时模型配置但不提供密钥。若失败为 Schema/一致性错误，进入 S25-01 结构化结果解析与模型响应契约复盘；若为 timeout/transport/http status，优先调查 Ollama 服务、模型资源与并发配置。无证据时不增加重试。
如果请求成功，则执行 C02 真实工具失败→修正→成功验证，记录 Trace；**成功一次不表示该 503 稳定性问题已关闭**。

当前状态：C02 **BLOCKED / INVESTIGATION**，S25-02 Step 5 未完成。