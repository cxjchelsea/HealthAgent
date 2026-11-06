# S25-05 前置收口 02 — 路由、Shadow、身份配置核验

审查对象：`main@cdde8ebb6c278dda752a744f75051159285ff65b` 中的 `agent/config.py`、`apps/api/main.py`、`.env.example`；日期：2026-11-06。

**证据边界：本文件核对的是 GitHub 已提交的代码和示例配置，不是用户 Windows 工作站 `D:\HealthAgent\.env` 的真实内容。** 不得声称已读取或修改本地私密配置，也不得粘贴 `MODEL_API_KEY`、`USER_TOKENS` 等秘密。

## 1. 已核实的代码事实

| 环境条件 | 实际生效模式 | Primary 入口 | 说明 |
| --- | --- | --- | --- |
| `MODEL_PROVIDER=fake`, `ROUTING_PROFILE=auto`，未显式配置旧模式旗标 | `legacy` | 不启用 | Fake 保留测试用兼容路径 |
| 真实 Provider（如 `ollama_proxy`），`ROUTING_PROFILE=auto`，且没有显式旧路由覆盖字段 | `unified` | 启用 | 新默认 Primary；不是额外语义审查器 |
| 真实 Provider，显式 `ROUTING_PROFILE=unified` | `unified` | 启用 | 强制 `guarded/structured/M5 unified true/M5 first false`；冲突旗标拒绝启动 |
| 真实 Provider，显式 `ROUTING_PROFILE=legacy` | `legacy` | 不启用 | 有意启用旧兼容路径 |
| `auto` + 显式设置旧 `ROUTE_ADMISSION_MODE`、`UNDERSTANDING_STRATEGY` 或 M5 旗标 | 由实际旗标决定，可能 `legacy`、`compatibility`、`unified` 或配置错误 | 不得推定 | 尤其在 `auto` + 显式旧旗标下，即使计算结果为 `unified`，`routing_profile_source` 也不是 `auto_real_provider_default`，Primary 不会自动启用；无效组合报错 |
| Fake + 显式 `PRIMARY_ROUTE_V1_CANARY_ENABLED=true` | 必须满足 Canary 的专门 Host 约束 | 仅显式 Canary | 属于测试机制，不是普通生产路由 |

`ROUTING_PROFILE=unified` 不是直接准许任意工具：它只改变主语义路由的启用条件。任何工具执行仍要经过 Host 和工具层授权。

- `ROUTE_SHADOW_ENABLED` 默认 `false`，`S25_04_ADMISSION_TRACE_ENABLED` 默认 `false`。Shadow 仅用于影子诊断，不得用于授权；性能基准关闭不必要的 Shadow，并分别记录模型调用开销和实际路由。
- `PENDING_WRITE_CONFIRMATION_ENABLED` 显式配置默认 `false`，但**真实 Provider 的 Primary 启用时**，计算属性 `pending_write_confirmation_active` 仍为 `true`；不能仅凭原始环境变量判断确认链路关闭。
- `ALLOW_DEMO_IDENTITY` 默认 **true**，`USER_TOKENS` 默认空；没有令牌映射时请求使用 demo-local 身份。这个开发默认值不等于生产认证。
- **重要**：仓库 `.env.example` 包含 `UNDERSTANDING_STRATEGY=direct`。若照搬 `.env.example` 后只修改 `MODEL_PROVIDER=ollama_proxy` 且保留 `ROUTING_PROFILE=auto`，该显式旧旗标会阻止自动 Primary 默认切换。测试/开发建议显式 `ROUTING_PROFILE=unified` 并移除冲突旧配置，而不是盲信 `auto`。

## 2. 本地安全核验指令（PowerShell，不回显秘钥）

在你自己的 Windows PowerShell 终端运行。脚本只打印配置的**类别、布尔值和来源**；不会输出敏感令牌、密钥或数据库 URL。不自动更改 `.env`。

```powershell
Set-Location D:\HealthAgent
.\.venv\Scripts\Activate.ps1
git rev-parse HEAD

@'
from agent.config import Settings
from pydantic import ValidationError
fields = (
    "model_provider", "routing_profile",
    "route_admission_mode", "understanding_strategy",
    "m5_unified_route_enabled", "m5_first_route_enabled",
    "primary_route_v1_canary_enabled",
    "route_shadow_enabled", "s25_04_admission_trace_enabled",
    "pending_write_confirmation_enabled",
    "allow_demo_identity",
)
try:
    s = Settings()
    for key in fields:
        value = getattr(s, key)
        # Do not print raw provider endpoints or secret env values.
        print(f"{key}={value}")
    print("effective_routing_profile=" + s.effective_routing_profile)
    print("routing_profile_source=" + s.routing_profile_source)
    print("primary_route_v1_active=" + str(s.primary_route_v1_active))
    print("pending_write_confirmation_active=" + str(s.pending_write_confirmation_active))
    print("has_development_user_tokens=" + str(bool(s.user_tokens)))
except ValidationError:
    print("CONFIG_INVALID: Settings validation failed; inspect local route flag compatibility without sharing secrets")
'@ | python -
```

**本地验收门禁：** 真实模型的 Unified 环境必须显示 `effective_routing_profile=unified`、`primary_route_v1_active=True`、`pending_write_confirmation_active=True`。`routing_profile_source` 应为 `explicit_unified` 或 `auto_real_provider_default`。若相反，不要继续 S25-05 真实模型测试，先排除显式旧旗标冲突。

## 3. 建议配置策略（不自动覆盖原 .env）

开发/验收真实 Qwen：在隔离配置中使用 `MODEL_PROVIDER=ollama_proxy`、`ROUTING_PROFILE=unified`，移除 `UNDERSTANDING_STRATEGY=direct`、`ROUTE_ADMISSION_MODE=legacy`、旧 M5 不一致覆盖，并优先 `ROUTE_SHADOW_ENABLED=false`。保持原有有效代理连接参数。

Fake 回归：保持 Fake 及其既有 fixture 路由，不强制改为真实 Provider。

生产环境：要求 `ALLOW_DEMO_IDENTITY=false`，但**仅此还不够**：现有 `USER_TOKENS` 机制只适用于受限开发测试；生产鉴权、安全代理、TLS 与用户隔离是部署前独立门禁。生产上线、PR 合并和本地测试是三个不同决策。

## 4. 暂不在本工作包修复

不批量删除 Legacy flags，不改变 Shadow 工作方式，不升级认证系统，不改 `.env.example` 的历史样例以免触发无意路由迁移。S25-05 实现时必须对环境条件做覆盖测试，不允许通过 Registry/Invoker 绕开现有 Host。
