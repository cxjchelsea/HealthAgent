# S25-01 — Windows Docker Ollama HTTP 502 与结构化理解验收故障复盘

## 复现证据（2026-10-22）
Windows Docker Desktop 容器中的 Ollama 已安装 qwen3:4b 与 qwen3:14b，GPU 可识别；PowerShell Invoke-RestMethod 请求 /api/chat 成功，Python httpx.post 使用默认参数访问同一地址得到 HTTP 502（空响应），改成 httpx.Client(trust_env=False) 则 HTTP 200。

同一环境第一轮 Direct E2E 5/5 HTTP 503，Structured 5/5 失败。排除代理干扰后 Direct 5/5 HTTP 200，Structured 4/5 HTTP 503，证明代理与结构化输出是**两个独立问题**，不能归因于模型尺寸或 GPU。

## 根因与技术解释
httpx 默认 trust_env=True，读取系统 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY 等环境变量；Windows 用户环境下代理绕路可能将 loopback 本地请求送往代理，得到 502。此处由 trust_env=False 成功复现隔离，因此属于已验证的代理环境影响。Structured 模式持续失败的精确 Schema 错误尚需增强诊断后再次实测，不能宣称已经证明具体提示词根因。

## 修复策略
- 为原生 Ollama loopback（localhost/127.0.0.1/::1）请求禁用 httpx 环境代理继承（trust_env=False），涵盖流式生成、工具决策、Understanding；不影响非本地服务与 OpenAI-compatible 远程服务的代理。
- 统一 `agent/http_policy.py` 作决策；不需要用户在 PowerShell 手动设置 NO_PROXY，也不全局清空系统代理。
- 使用 Ollama 原生 API 的 MODEL_BASE_URL=http://127.0.0.1:11434/api，适配器追加 /chat。
- 对 Structured 增补机器可读 JSON Schema 请求、分层错误分类与有限重试：4B 最多尝试一次，14B 遇 Schema/一致性错误时最多重试一次；对于伪造 goal ID 或传输异常不在同模型上重试。失败尝试仅记录错误类别与耗时，不记录原始用户文本或模型输出；保持无关键词 fallback、不得绕过用户确认和目标授权。

## 验证与判定
确定性单元测试用于验证代理选择和 Structured 校验；真正的网络复现应在 Windows Docker Ollama 环境使用两模型重跑：
```powershell
cd D:\HealthAgent
git pull --ff-only
python -m evals.s25_01_live --output s25_01_live.json
python -m evals.s25_01_local_e2e --output s25_01_e2e.json
Get-ChildItem .\evals\results\s25_01
```
若远程 Ollama 使用代理，保留正常系统代理环境的支持；本地代理绕行规则只针对 loopback。未取得新报告之前不能判定 Structured 通过或修改默认策略；PR #17 保持 Draft。
