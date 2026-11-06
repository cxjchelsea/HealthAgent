# S25-05 Step 4 — Slice B1: migrate ordinary calculate behind opt-in

Status: IMPLEMENTED ON FEATURE BRANCH; EXACT-HEAD CI AND REAL MODEL ACCEPTANCE REQUIRED.

## Scope
- `calculate` is registered as a trusted read definition, version `1.0.0`, using existing `CalcArgs` and a compatibility adapter that delegates to existing `execute_tool`. Arithmetic semantics and error observations remain unchanged.
- Server-side Host policy controls the new grant with `S25_05_CALCULATE_REGISTRY_ENABLED=false` by default. Only `ordinary_tool + read_only + allow` may grant it.
- Ordinary AgentLoop calls Registry only when that trusted grant is present. No new calculator tool is appended to an existing exposure if semantic filtering/Host allowlists removed it, and duplicate provider definitions are avoided.
- Saved plan, M4 journal/dispatch, checkpoint recovery, legacy/fake paths without the opt-in, and write tools are unchanged. A calculator registry migration is NOT an authorization expansion.
- Tests check opt-in grants, success and existing calculation errors, exposure reduction, provider tool de-duplication, and successful ordinary tool observations.

## Remaining Step 4 scope
- Complete independent negative testing and exact-head CI review.
- Migrate remaining read-only tools one by one with their real external state dependencies and error contracts (`utc_time`, `list_tasks`, `recall_memory`, `mcp_reference_lookup`).
- Versioned checkpoint/plan recovery design, M4 dispatch compatibility, trusted MCP catalog verification and realistic provider tests remain unfinished.
- Retain PR #33 as Draft; no merge without separate authorization.

Acceptance warning: existing automated scripted models are NOT a substitute for real `ollama_proxy/qwen3:14b` validation.
