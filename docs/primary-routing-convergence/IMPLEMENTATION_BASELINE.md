# Primary Routing Convergence — 实施基线

- Base: `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`
- Branch: `refactor/primary-routing-convergence`
- Status: baseline frozen; implementation not yet accepted
- Contract: AC1–AC5 v1.2, frozen 2026-11-03. Contract text shall not be weakened to satisfy failing tests.

## Versioned route contract

`schema_version=primary-route-v1` (strict; unknown versions and enums denied).

Canonical `route_kind`:
`plan_create`, `plan_revise`, `plan_execute_first`, `plan_resume`, `plan_cancel`, `plan_missing`, `ordinary_tool`, `respond`, `clarify`, `clarify_response`, `reject`.

Legacy adapter is explicit and version-bounded: `create→plan_create`, `revise→plan_revise`, `execute_first→plan_execute_first`, `resume→plan_resume`, `cancel→plan_cancel`. M5 `other` MUST NOT be silently mapped to ordinary_tool/respond/clarify. `plan_missing` needs Host-proven missing referent; a model guess is not proof.

## Acceptance contract

**AC1 — Single Primary semantic owner.** One authoritative route proposal per Unified Turn; `UnderstandingService` and `decide_execution_policy_detail` must be folded into the Primary or treated as a failing second semantic decision. AgentLoop’s post-admission multi-turn tool choices are out of scope, but cannot expand the approved route. Limited schema retries belong to the same owner.

**AC2 — Zero legacy model reviewers in Unified execution.** Fail on ANY call to the following exact thirteen functions (including renamed or wrapped equivalents): `review_plan_cancellation`, `propose_first_route`, `review_plan_intent`, `check_plan_edit_intent`, `classify_pending_reply`, `review_pending_reference_lookup`, `review_unsupported_capability`, `review_read_only_clarification`, `reconcile_execution_policy`, `review_plan_creation_reference`, `review_missing_plan_reference`, `review_plan_execution_authorization`, `review_plan_execution_scope`. `review_plan_revision` is permitted ONLY after `route_kind=plan_revise` has been determined and only for referent/step/argument binding; it must not reclassify route. `propose_new_plan_route` and `propose_saved_plan_route` must ultimately be consolidated into one semantic owner; they are not members of the thirteen. Legacy and post-commit advisory Shadow are outside Unified execution authorization. Test fail-on-call plus static audit, not just ordinary happy paths.

**AC3 — One logical deterministic Host admission** yielding allow/deny/clarify/restrict. Many internal checks (schema, permissions, CAS, provenance, scope, dispatch recovery) are allowed; a second semantic owner is not. Host may narrow but never enlarge authority or change `route_kind`. Once side effect occurred, dispatch outcome is unknown, or Turn committed: NEVER reclassify or switch to Legacy. Unknown means reconcile or fail closed.

**AC4 — Shared effect/dispatch fence.** AgentLoop, saved-plan execution, and pending continuation must obey compatible scope, authorization, idempotence, journal/CAS, replay and error evidence. Pending `create_task` and `mcp_reference_lookup` must be tested; the direct `execute_tool` at `apps/api/main.py:687–726` may not bypass governance. Never replay a write effect after unknown outcome.

**AC5 — Preserve functionality and safety.** Existing S25-01/02/03/04 scenarios must remain functional; refuse-all cannot pass. Include hidden/new semantic samples, full regression/CI, real Qwen/Proxy E2E, negative authorization and replay tests, accurate HEAD-bound evidence. Do not tune prompts on known failing samples and recycle the same cases as proof of generalization.

## Frozen main observed baseline

Three sequential real-provider HTTP suites on isolated SQLite, `ollama_proxy/qwen3:14b`:
- Suite 1: 10 requests, 15 AC2 function entries
- Suite 2: 11 requests, 11 AC2 function entries
- Suite 3: 2 requests, 2 AC2 function entries
- **Total: 23 requests, 28 AC2 entries** (observed Python function entries; not the number of underlying model HTTP calls; not exhaustive branch coverage).
- Demonstrated CREATE/FIRST/RESUME/REVISE/CANCEL paths.
- Pending MCP persisted `needs_input:mcp_reference_lookup`, then `verified_clarification_execution`; pending `create_task` write not proven.
- Saved-plan ordinary request produced `unresolved_tool_failure`: known regression-risk baseline, not an accepted success.
- PostgreSQL, process crashes and concurrent replay not established by baseline and remain required under AC4.

Source evidence (collected outside repository): `primary-routing-main-runtime-baseline.json`, `primary-routing-main-saved-plan-pending-baseline.json`, `primary-routing-main-pending-verified-baseline.json`.

## Four work packages

1. Single versioned Primary route proposal and semantic ownership.
2. Deterministic Host admission with trusted binding and scope checks.
3. Unified effect/dispatch governance for ordinary, plan, and pending continuations.
4. Legacy retirement and AC1–AC5 automatic + physical independent acceptance.

No merge to main without explicit PR merge authorization. Do not claim an AC passes until evidence is recorded.