# Primary Routing Convergence — archive index

**Archive status:** pre-implementation evidence and AC1–AC5 contract are committed to this feature branch. No business-path code is changed by this archive.

## Authoritative inputs

- [ACCEPTANCE_CONTRACT.md](ACCEPTANCE_CONTRACT.md): frozen **v1.2** acceptance contract; use this rather than the shorter overview when AC requirements differ in level of detail.
- [DECISION_CALL_GRAPH.md](DECISION_CALL_GRAPH.md): read-only call graph and legacy call disposition for original `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`.
- [IMPLEMENTATION_BASELINE.md](IMPLEMENTATION_BASELINE.md): short implementation overview, created before complete document archival.
- [evidence/main-static-baseline.md](evidence/main-static-baseline.md): original static call-site baseline. Its historical statement that runtime measurement was not yet available reflects the moment it was authored; see the JSON evidence below for later measurements.

## Recorded real-provider evidence (unchanged original JSON)

1. [First Unified physical suite](evidence/main-runtime-baseline.json): 10 HTTP requests, 15 observed AC2 entries.
2. [Saved-plan and pending scenario suite](evidence/main-saved-plan-pending-baseline.json): 11 executed HTTP requests, 11 AC2 entries; pending answer was skipped because setup did not establish required state.
3. [Verified pending suite](evidence/main-pending-verified-baseline.json): 2 HTTP requests, 2 AC2 entries; durable `needs_input:mcp_reference_lookup` followed by `verified_clarification_execution`.

**Combined historical observation:** 23 HTTP requests and **28 AC2 Python function entries**. This is not 28 underlying model HTTP requests and not exhaustive branch coverage. Original working trees were reported dirty; evidence is bound to the recorded HEAD, not a verified-clean local workspace. Saved-plan ordinary tool failed with `unresolved_tool_failure`; pending `create_task` write, PostgreSQL, crash, and concurrent replay were not proven by these baseline runs.

## Historical reproduction scripts

- [runtime_call_baseline.py](../../evals/primary_routing_baseline/runtime_call_baseline.py)
- [saved_plan_pending_baseline.py](../../evals/primary_routing_baseline/saved_plan_pending_baseline.py)
- [pending_verified_baseline.py](../../evals/primary_routing_baseline/pending_verified_baseline.py)

These scripts are preserved **verbatim** from the frozen-main audit, including their exact-HEAD checks. They are **not** new-branch acceptance tests and must not have the SHA guard weakened to run against the changed feature branch. To reproduce, use an isolated checkout/worktree at original `41d91a3...`, run the script with the repository root as working directory and Python import root, and provide an available `ollama_proxy/qwen3:14b` model. Example from the archived branch checkout for syntax inspection only: `python -m compileall -q evals/primary_routing_baseline`; this does not constitute physical acceptance.

## Implementation gate

1. Primary proposal with versioned `primary-route-v1` schema and single semantic ownership.
2. Deterministic Host admission; model classifiers do not independently alter intent.
3. Unified permission and dispatch evidence for plan/ordinary/pending tool execution.
4. Explicit retirement of AC2 reviewers from Unified execution, full tests and real-provider acceptance.

All changes after this archive go only to `refactor/primary-routing-convergence`. `main` must not be modified or merged without explicit authorization.
