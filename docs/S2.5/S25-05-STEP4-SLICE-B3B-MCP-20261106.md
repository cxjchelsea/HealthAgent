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
