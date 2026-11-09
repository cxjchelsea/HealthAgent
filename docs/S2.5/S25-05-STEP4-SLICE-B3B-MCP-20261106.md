# S25-05 Step 4 — Slice B3b: fixed trusted MCP catalog

Status: IMPLEMENTED ON DRAFT PR #33; exact-head CI and Qwen acceptance pending.

## Scope
- Existing `StdioMCPClient.call(name, arguments)` remains unchanged for Legacy, Pending and saved plans. The new `call_trusted_reference(arguments)` opts into a stricter, fixed service/catalog binding.
- Only the checked-in trusted `agent.mcp_stdio_server` subprocess is ever started, with a fixed `mcp_reference_lookup` mapping. Its identity `healthagent-trusted-reference@1.0.0`, protocol version `2025-06-18`, sole-tool name and `inputSchema` must match the hardcoded local policy. Untrusted `tools/list` cannot register or authorize extra tools, change schema, or introduce commands.
- Add `mcp_reference_lookup@1.0.0` to the read-only registry with existing strict `ReferenceArgs`; use an invocation-local trusted `StdioMCPClient`, never an arbitrary `MockMCPClient` or client supplied by a model.
- `S25_05_MCP_REFERENCE_REGISTRY_ENABLED=false` by default. Server Host must admit `ordinary_tool/read_only` for the opt-in grant.
- Retain fail-closed `mcp_catalog_mismatch`, `mcp_protocol_error`, `mcp_timeout`, `mcp_transport_error`, `mcp_untrusted_client` or existing reference errors without pretending a lookup succeeded.
- Keep Primary/Host, the Pending MCP provenance path, PlanLedger, checkpoint CAS and M4 dispatch untouched. No writes, shell execution, arbitrary URL or remote tool discovery are introduced.

## Tests
- Trusted local stdio lookup and not-found error.
- Untrusted client, extra arguments and absent runtime rejection.
- Controlled `tools/list` and initialization mutations: server-name mismatch, version mismatch, changed schema and injected extra tool.
- AgentLoop Registry opt-in on/off parity and duplicate-exposure checks.
- Entire previous B3a memory owner-bound HTTP suite, and full S25-01–04 regression CI, remain required.

## Acceptance caveat
A reported CI success is only evidence for the exact commit it tested. Qwen and PostgreSQL physical acceptance are Step 5 gates. A fixed-service identity is a local protocol allowlist, not cryptographic authentication of a different remote service.

## 2026-11-09 post-review repair: verify-before-call

Previous verified-client code sent `initialize`, `tools/list`, and `tools/call` in one batch, then checked the returned catalog. This validated the eventual result but could not prevent a call reaching an untrusted catalog.

The verified path now uses **one persistent subprocess** with ordered protocol stages: initialize and check protocol/server identity; send initialized notification and request tools/list; check the fixed trusted tool name and inputSchema; only then send tools/call. Any negative stage exits without a tool call. Legacy `StdioMCPClient.call` remains unchanged.

Acceptance evidence needed at **exact PR HEAD**:
- Mutation cases (server identity/version, protocol, schema, injected extra tool) assert `tools/call` is absent from transmitted requests.
- Good-catalog case asserts ordered `initialize -> initialized -> tools/list -> tools/call`, and real local stdio call tests retain the prior observations.
- CI passing is required; real Qwen/PostgreSQL checks remain Step 5.
- Do not merge PR #33 without separate permission.

## Next interface decision: ToolInvoker without Host authority transfer

Do **not** make `TrustedReadRegistry.invoke` the executor for saved plans/M4 yet. The next design review should introduce a minimal, immutable invocation context, carrying a trusted Host-issued grant, tool id/version, exposure scope, and execution context; execute only after Host checks. `ToolInvoker` may validate and dispatch, but cannot assign authority, create approvals, advance plan steps, or finalize CAS journals. Existing ordinary, saved-plan, and M4 recovery call sites require a call graph and invariants review before migration.

Persistence design: introduce an explicit tool-version binding in future attempts/checkpoints (with migration), and preserve historical unknown-version records as unknown instead of rewriting them to the current version. All prepared/replayed evidence must remain tied to its original tool identity and invocation contract.
