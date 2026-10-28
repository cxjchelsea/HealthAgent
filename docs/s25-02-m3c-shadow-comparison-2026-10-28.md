# S25-02 M3-C B — Shadow comparison protocol (2026-10-28)

Status: CI / simulated model validation only. NO production cutover. No live Qwen claim.

## Current implementation

- `evals/s25_02_m3c_shadow_comparison.py` accepts either one formal API response containing `route_shadow` or a JSON report with `rows`.
- It treats exact route matches, compatible but unknown saved-plan execution scope, mismatches, unavailable evaluations and evidence gaps separately.
- Any comparison remains `readiness: HOLD`; comparing a terminal legacy marker with a model route is observational, not ground truth.
- `diagnostic_model_calls=1` counts one shadow evaluation attempt, not every HTTP model call or the legacy path.
- No local Ollama access exists from CI. Live Qwen3 cannot be inferred from Fake Provider or monkeypatched runs.

## Local Windows validation

Start the API with a configured real Ollama proxy model, structured understanding and `ROUTE_SHADOW_ENABLED=true`.
Use a fresh conversation per independent task; for plan continuation and revision, reuse the originating conversation.

Run per scenario from PowerShell:

```powershell
$base = "http://127.0.0.1:8000"
$cid = (Invoke-RestMethod -Uri "$base/conversations" -Method Post).id
$body = @{ content = "请用计算工具计算 (19+23)*4"; client_request_id = [guid]::NewGuid().ToString() } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$base/conversations/$cid/agent/messages" -Method Post -ContentType "application/json" -Body $body
$response | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 "m3c-response.json"
python -m evals.s25_02_m3c_shadow_comparison --input "m3c-response.json" --output "m3c-comparison.json"
Get-Content "m3c-comparison.json"
```

Repeat with saved-plan create/no-execute, execute first, resume remaining, revise step, standalone MCP, create/list task, pending clarification, missing saved plan, unsupported capability and write denial. Re-run wording variants with separate request IDs. Preserve response files and actual tool/plan state evidence. Inspect `route_shadow.state`, `comparison`, identity and snapshot diagnostics, and `diagnostic_elapsed_ms`.

## Remaining acceptance blockers

1. Capture actual **legacy model HTTP call counts** (including retries) separately from shadow; currently there is no equivalent exact counter.
2. Measure both paths' latency distribution using real Qwen3.
3. Verify first-step vs remaining-step plan scope with explicit Host-level evidence; a `compatible_plan_execution` result is not strict equality.
4. Cross-route tests currently use deterministic fixtures. Run full live API scenario matrix including concurrency, consent and checkpoint version changes.
5. No approval for production route switching or merging.
