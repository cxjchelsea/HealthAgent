# S25-07 Gate B — Slice B1 implementation handoff (2026-11-12)

**Base:** `main@58e6edade0eacaeee68a122d4cfcc93a9aac4a3d` (Gate A / PR #50 merged). Design source: PR #49, not merged. This slice is a new independent Draft PR.

## Actual B1 scope

Adds a pure `compile_revision_candidate(expected_raw, replacement_pending, current_user_message)` function, returning `RevisionCandidate`. It does **not** write the DB, grant Host permissions, change Primary routing, call the LLM, or dispatch tools. The revision candidate's `dependency-plan-read-replan-v1` executor version is **deliberately unsupported** by S25-06; attempts to treat it as an executable saved plan must fail closed.

- Strictly validate the exact S25-06 parent `dependency-plan-v1 / dependency-plan-read-v1 / revision=1` with the unchanged runtime validator.
- Complete prefix is copied from the parent; model can propose only an equal-length, fixed-ID/order/dependency **and fixed expected-tool** pending suffix. Switching even between the two registered read tools is denied until a separate Host equivalence/substitution contract is implemented. No new tool permissions or version.
- Strict Pydantic validation of every step, plus topological and observation-binding contract revalidation.
- When a *changed* step introduces a literal input, its `source_quote` and literal value must be grounded in the current user message. Upstream observation text cannot substitute for new consent.
- No-op revision is rejected.
- Proposed `revision=2` retains historical completed-step `executions`, including origin `plan_revision=1`, and generates a tentative `lineage` with exact parent snapshot SHA-256 and origin refs. **This lineage is not yet DB-certified.**
- Initial creation `DependencyPlanCreation` and legacy format are unchanged.

## Limitations blocking the broader Gate B

1. **B2 — Host authority and atomic save:** Need a new Primary `plan_revise` decision contract, separate immutable PrimaryRouteEvidence for current Turn, exact old snapshot + absence of active Claim, no unresolved Dispatch, no reusing failed step, DB-verified completed prefix, CAS update, task-state transitions, and PG 1-winner conflict proof.
2. **B3 — versioned read execution:** Current `_step_for_read`, `resolve_checkpointed_definition`, `dependency_dispatch`, `checkpoint_dependency_step_in_session`, and operator-only `dependency_recovery` require the old executor/revision lineage. All must opt into a distinct revised validator with full source Receipt/Attempt/Dispatch revalidation before any revision-2 RESUME. Do not simply replace equality with `<=`.
3. **Gate C:** `failure_finalized` steps are still blocked against duplicate dispatch. B1 does not resolve automatic same-step retry, ordinal migration or tool substitution.
4. **Gate D:** Real provider model revision proposals and official HTTP E2E are not present in B1. This is a deterministic testable contract only.

## Next implementation order

**B2a:** design a bounded, no-dispatch Host revision permit and an atomic transaction that rechecks every `lineage` record under the same locks; model proposal is not authorization. **B2b:** integrate Primary and official HTTP with stable turn semantics. **B3:** introduce revised runtime execution + cross-revision provenance and prove PG concurrency/fence. Then Gate C.

**Status:** B1 implementation proposal only. Do not mark Gate B complete or merge without explicit user authorization.
