# S25-05 Step 4 — Slice B2: UTC and conversation task-list compatibility

Status: implemented on feature branch; exact-HEAD CI and real-model acceptance pending.

## Scope
- Add `utc_time` and `list_tasks` to trusted read registry as versioned definitions using the existing strict `EmptyArgs` schema, with adapters delegating to the established `execute_tool` contract.
- `ReadToolRuntime` carries only trusted execution-local dependencies. UTC retains the injectable clock. `list_tasks` receives only the current AgentLoop's conversation-scoped task store, or the original local tasks list for legacy isolated tests. No global task store or model-supplied owner ID is introduced.
- Each migration is independently opt-in using `S25_05_UTC_TIME_REGISTRY_ENABLED=false` and `S25_05_LIST_TASKS_REGISTRY_ENABLED=false`. The Host must still allow `ordinary_tool/read_only`.
- The registry grant does not add migrated tools back to model exposures filtered out by the original Host allowlist, S25-04 selection, or explicit operation. All registered invocations still check trusted name, exposure, argument schema and version.
- Existing saved plans, M4 recovery and write tools remain on their established paths; this change cannot authorize any writes.

## Evidence
- Regression tests cover server Host grant denial, missing runtime context, injected UTC clock, isolated task stores, model exposure de-duplication and normal old/new observation parity.
- CI for exact final commit must pass before this slice can be marked accepted. Scripted models do not constitute real Qwen validation.

## Remaining scope
- Real HTTP owner-boundary, persistent task readback and Qwen observation tests remain acceptance gates; no claim of production readiness.
- Remaining legacy reads `recall_memory` and `mcp_reference_lookup` require explicit context and MCP trust verification.
- M4 plan invoker, versioned checkpoint migration and trusted MCP catalog are future Step 4 slices.
- PR #33 remains Draft. No merge without explicit authorization.
