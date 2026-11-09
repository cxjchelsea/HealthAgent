# S25-05 Step 4 — Slice B3a: owner-scoped memory read migration

Status: IMPLEMENTED IN DRAFT PR #33; latest-head CI and real-model validation still required.

## Scope
- Register `recall_memory` as a versioned trusted read tool, using the existing `MemoryKey` schema and legacy `execute_tool` observation compatibility.
- Add `S25_05_RECALL_MEMORY_REGISTRY_ENABLED=false` by default. Only server-origin Primary ordinary read and Host allow/read_only can grant Registry execution.
- Pass the existing Host-created owner-scoped `MemoryStore` as an invocation-local dependency; no model-provided owner IDs, no global default memory, no write adapter.
- Preserve `save_memory` and consent transaction ownership under Host; saved plan, M4 and checkpoint paths remain unchanged.
- Tests exercise Host denial, absent runtime, absent memory store, invalid extra keys, real SQLite per-owner read isolation, ordinary AgentLoop behavior and de-duplication of model-visible schemas.

## Boundaries
- Registry does not independently authenticate a user or derive owner_id. Only the API Host is authorized to supply a correctly scoped MemoryStore.
- Memory values remain excluded from the durable Attempt projection by the existing `bounded_projection("recall_memory", ...)`. Trace and model tool observation may contain the user-owned value as before.
- No MCP trust expansion is included; `mcp_reference_lookup` will require a separate trusted-catalog compatibility review.
- Existing Fake/Legacy defaults unchanged. This document is not evidence of real Qwen or PostgreSQL physical acceptance.

## Gates
- Check exact HEAD GitHub CI and targeted read memory regression tests.
- Add real HTTP owner authorization tests if not already covered by current S25-02/03 full regressions.
- Keep Draft PR unmerged until Step 4 full scope and later Step 5–6 reviews finish.
