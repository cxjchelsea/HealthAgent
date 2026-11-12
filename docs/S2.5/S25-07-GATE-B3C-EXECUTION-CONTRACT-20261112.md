# S25-07 Gate B3c — revision-2 bounded Dispatch/Checkpoint design contract (DRAFT)

**Baseline:** `main@b9b9ed1481b2ff00f3bce56551b26cc38277c53d` (PR #57 merged).

> **Review supersession:** the initial draft's universal lock order in I04 and open H1–H5 are superseded by `S25-07-GATE-B3C-INDEPENDENT-DESIGN-REVIEW-20261112.md` and the more precise `S25-07-GATE-B3C1-PREIMPLEMENTATION-SPEC-20261112.md`. Do not implement I04's stated lock order literally: actual existing receipt/checkpoint paths differ. The review's R1–R7 must be observed.  
**Status:** design only, no runtime authorization, no feature enabled, no production readiness. Review and explicit implementation authorization required.

## 1. Objective and exclusions

Implement *internal-only*, guarded, read-only execution of the **first eligible pending step** of an approved `dependency-plan-read-replan-v1`, revision 2, while retaining revision-1 completed evidence. Persist a single durable pre-dispatch intent before any external tool call; consume only a trusted M4 observed Receipt; atomically checkpoint plan, Claim, Dispatch, Attempt and S25-03 TaskState/Transition.

No official HTTP wiring (B3d), no automatic retry or unknown-outcome replay (Gate C), no broader DAG, no arbitrary tool or user-authorization synthesis, no production operator attestation. `S25_07_REVISION_RESUME_PREPARE_ENABLED=false` remains default off. An additional execution-specific opt-in must remain default off and inaccessible from public HTTP in B3c.

## 2. Existing boundaries actually inspected

- `agent/dependency_revision_host.py`: `PreparedRevisedRead` is inert and only binds a pristine revision-2 plan; in-process Host decision carries `approved_request_digest`, **not** persisted.
- `services/dependency_revision_lineage.py`: `verify_revised_lineage` asserts exact pristine revision-2 projection, approved revision SHA and revision-1 origin records; it cannot validate *after* a revision-2 checkpoint.
- `services/dependency_dispatch.py`: v1 dispatch exclusively requires `PreparedDependencyRead`, locks Plan -> Claim -> Fence, checks Host and duplicate/unresolved Dispatch; v2 object must never be passed to this function.
- `services/dependency_execution.py`: v1 checkpoint consumes recorded M4 Receipt and evolves task projections; v2 needs separately version-aware origin/revision evidence.
- `services/dependency_fencing.py`: generation token is non-expiring; only independently verified operator recovery can revoke it.
- `services/primary_route_evidence.py`: write-once Primary evidence has `approved_revision_digest` for plan_revise, **not** durable approved resume request SHA.

## 3. Required invariants — all fail closed

**I01 — Immutable authorized request:** persist a non-null, SHA-256 `approved_request_digest` for the exact resume Turn user Message when and only when the same Primary proposal and deterministic Host decision approve `dependency_revision_resume_prepare_only`. A DB constraint / migration-compatible nullable column may be used, but a legacy NULL is denied for v2 execution. Host decision, current Turn user_message_id, Message text hash, Turn request_id, conversation and owner must all agree. Evidence row must be write-once; never backfill by inferring from an existing allow row. Persist authorization before creating Dispatch; no model-provided evidence.

**I02 — Stable revision origin:** bind execution to the immutable B2b3 revision approval Turn and `approved_revision_digest`, its parent snapshot SHA, `origin_refs`, and all revision-1 actual Dispatch/Attempt/Receipt records. The initial approved candidate fingerprint must remain independently verifiable *after* the plan snapshot changes at checkpoint. Do **not** recompute authorization against the mutable current plan and call it original approval. Store or derive a cryptographically bound immutable candidate anchor; spell out exact schema & migration before implementation.

**I03 — Version-aware physical execution:** distinguish inherited checkpointed revision-1 evidence from newly executed revision-2 steps; validate both on every dispatch/checkpoint. An inherited source must use the **original physical** definition, with pinned tool version, arguments digest, source Dispatch, succeeded Attempt and bounded Receipt projection. A new v2 source is usable only after its own verified checkpoint. Never accept model-supplied observation text or unverified JSON snapshots.

**I04 — Single atomic dispatch admission:** within one DB transaction acquire lock order **ConversationPlan -> PlanExecutionClaim -> DependencyWorkerFence -> PlanToolDispatch/AgentAttempt -> AgentTaskState** (consistent with existing lock hierarchy; where needed include preexisting immutable approval row); re-evaluate owner, running Turn, user request digest, exact pre-Turn/Claim snapshot, revision anchor, current step, first pending position, available trusted sources, tool version/schema, arguments digest, active worker fence, and absence of unresolved or duplicate logical-step Dispatch. Only then insert a version-2 `dispatch_unknown` row. Commit *before* tool invocation. No success can be claimed by merely inserting intent.

**I05 — One logical step, at most one dispatch before Gate C:** in B3c reject **any** existing Dispatch with the same `conversation_id + step_id` regardless of `plan_revision`, including failed/unknown histories; distinguish legitimate completed revision-1 historical prefix from a forbidden Dispatch on a pending v2 step. DB locking / uniqueness and PostgreSQL concurrent contender tests must protect the absence check. No retries, replacement attempts, or dispatch deletion.

**I06 — Atomic checkpoint:** under the same lock/fence discipline, consume exactly one trusted M4 observed-success Dispatch + receipt; require Dispatch ownership, generation/token fingerprint, exact tool args & source digest and no preexisting successful Attempt. Atomically mark current step completed, append immutable execution record explicitly tagged `plan_revision=2`, mark Dispatch checkpointed, add succeeded AgentAttempt, advance TaskState revision and StepTransition, and update Claim snapshot. All-or-nothing rollback on conflict. No result from a model string or pre-dispatch preparation is eligible.

**I07 — Unknown and negative results:** `dispatch_unknown`, missing Receipt, timeout, external crash and unverified worker death prohibit automatic replay. A verified negative Receipt follows a distinct failure-finalization path with failed Attempt/TaskState evidence but no successful checkpoint or automatic replacement Dispatch. B3c must not accidentally open Gate C retry.

**I08 — Cross-step continuation:** after one v2 checkpoint, recompute and verify the *current* plan/TaskState projection against immutable approval plus full physical history; do not reuse the pristine-only `verify_revised_lineage` as-is. Current Turn must not silently authorize new user instructions. Fresh resume requests require their own Primary/Host approval and Claim as appropriate. B3c may deliberately limit itself to one v2 checkpoint per Turn, deferring HTTP multi-turn orchestration to B3d.

**I09 — Version isolation:** `PreparedRevisedRead` must remain rejected by v1 `begin_dependency_dispatch_in_session` and v1 checkpoint. Distinct v2 types/APIs; old plan executor and legacy PlanLedger stay unchanged. S25-06 route default unchanged.

**I10 — No evidence laundering:** `PreparedRevisedRead`, B3a proof, and test fixtures are read-only observations; never durable tool permit. All authorization is derived again from locked DB rows at dispatch and checkpoint. `primary_route_evidence` remains auditable without raw message, quote, secret or model answer storage.

## 4. Recommended implementation slices

- **B3c-1 — Durable authorization & immutable revision anchor:** schema/migration + write-once Host resume request SHA + approved candidate origin identity; negative migration/tamper tests. No Dispatch yet.
- **B3c-2 — Version-aware locked dispatch:** separate `begin_revised_dependency_dispatch_in_session`, preserving first-pending and source verification, worker fence and no-replay; tests SQLite + actual PostgreSQL concurrent sessions. Does not call tool.
- **B3c-3 — Receipt/checkpoint & progressed-lineage validator:** separate v2 checkpoint function; verified receipt consumption, atomic Plan/Claim/Task/Transition updates, immutable parent approval validation after one v2 step. Fail all partial writes.
- **B3c-4 — Internal runner acceptance / independent audit:** controlled read registry only, effective default-off isolation, migrations, crash/unknown negative paths, targeted/full CI; actual HTTP remains disabled. No merge before exact-HEAD independent review and explicit `AUTHORIZE MERGE #...`.

## 5. Required acceptance matrix

1. pristine v2 no completed prefix -> literal MCP first step -> observed-success -> v2 checkpoint; no duplicate dispatch.
2. inherited v1 MCP completed -> v2 text_stats uses original physical definition -> checkpoint; original v1 Dispatch/Attempt/Receipt unmodified.
3. progressed v2 MCP -> v2 text_stats uses v2 checkpointed definition; no reconstruction from a model string.
4. wrong owner/conversation/Turn/request_id/user_message_id or text/request SHA -> reject, zero writes.
5. forged Host approval, missing durable SHA, swapped candidate/parent digest, altered origin Dispatch/Attempt/Receipt -> reject, zero writes.
6. stale pre-Turn/current plan/Claim, wrong step, wrong tool/version/args, inactive or old Fence -> reject.
7. competing PostgreSQL workers may yield **one** dispatch at most; never double invoke.
8. unknown/negative Receipt, crash between dispatch and observation, receipt/Attempt substitution -> no automatic replay, no successful checkpoint.
9. checkpoint DB error -> rollback across Plan/Claim/Dispatch/Attempt/TaskState/Transition.
10. default-off, official HTTP isolation, v1 executor type isolation and full existing tests.
11. after checkpoint, current projection and immutable revision approval are both verified; negative tamper/replay tests.

## 6. Design-review HOLD points before implementation

- **H1:** concrete approved-candidate anchor after the first v2 checkpoint: exact storage field/table, uniqueness, migration, source of truth and DB transaction boundary.
- **H2:** current `PlanToolDispatch` has no explicit plan revision in the observed v1 API; establish a verifiable v2 dispatch discriminator and ensure historical v1 Dispatches remain valid.
- **H3:** current B3a validator checks a pristine TaskState and exact candidate fingerprint; identify precisely which checks remain valid and which need a new progressed-state verifier.
- **H4:** prove concurrency protection on PostgreSQL under actual transaction isolation and an explicit lock/unique-key strategy; SQLite success alone is not sufficient.
- **H5:** decide whether B3c accepts exactly one step per Turn (recommended) and state what B3d must add for further user-authorized RESUME.

**Decision:** Proceed with B3c-1 design and tests only after these HOLD points are settled; do not enable revision-2 execution in this design PR.
