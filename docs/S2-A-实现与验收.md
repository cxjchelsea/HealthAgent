# S2-A — Native Tool Calling + Bounded LangGraph Loop

## Scope
- Independent POST /agent/runs entry; legacy S1 /conversations remains unchanged.
- Prompt version `s2a-core-v1`, loaded from agent/prompts.
- Native Ollama /chat tools or OpenAI-compatible /chat/completions tools. Provider output must contain tool_calls; text looking like JSON is not treated as tool calls.
- LangGraph decide → tool observations → decide; hard decision step budget, max four tool calls per iteration.
- Allowlisted, typed tools: arithmetic (AST whitelist), UTC clock, run-local simulated create_task/list_tasks.
- Each response records run_id, terminal status, prompt_version and sanitized trace (tool names and success, no raw tool arguments).

## Safety and limitations
- No host command execution, no network tools, no medical decisions, no cross-run persistent tasks.
- The S2-A endpoint is demo-only under current development bearer-token identity; do not expose to the internet.
- S1 SSE is unchanged. S2-A endpoint currently responds with final JSON, not token streaming.
- Native Ollama gateways differ; live Qwen verification is required. A provider that rejects tools will fail rather than silently falling back to keyword heuristics.
- Tool observation message envelopes can require provider-specific adapter adjustments after live testing.

## Acceptance gates
1. Default pytest deterministic multi-step tool, injection attempt, invalid arguments, step limit, isolated tasks tests.
2. CI build of LangGraph package and all regression checks.
3. `RUN_S2A_LIVE=1 pytest tests/test_s2a_live_optional.py -q -m live` with configured Qwen3:14b; successful calculate tool call required.
4. 20 diverse executable tasks and failure-path evaluation after the live provider passes. Without this data, S2-A is implementation-ready, not CLOSED.
