# S25-07 Gate B3c — independent design review, 2026-11-12

**Reviewed:** PR #58 design `2bfc21f` against merged `main@b9b9ed1481b2ff00f3bce56551b26cc38277c53d`.  
**Verdict:** initial contract is **NOT implementation-ready as written**. This decision record makes H1–H5 concrete, but migration/physical concurrency tests remain implementation gates. Design-only, no execution flag.

## Review findings — actionable

**R1 (P1, contract correction): incorrect universal lock order.** Draft I04 says Plan -> Claim -> Fence -> Dispatch/Attempt -> Task. Real `services/dependency_dispatch.py` locks Plan -> Claim -> Fence and checks Dispatch, while `services/dependency_execution.py` locks Plan -> Claim -> **Dispatch -> Fence**, and `services/plan_dispatch.py::record_receipt` locks Dispatch then fence. Merely imposing draft order on a new code path does not establish global compatibility. Establish and test a **consistent transactional lock protocol** for *all interacting v2 writers*, considering receipt writer and recovery; do not change v1 lock order as an undocumented side effect. Decide whether the receipt writer must be specialized for v2. Concurrent worker/receipt/checkpoint/fence/inspection tests must prove no deadlock or document bounded retry on DB deadlock as a **transaction retry only, never tool replay**.

**R2 (P1): uniqueness is insufficient.** `plan_tool_dispatches` only has unique `(turn_id, step_id)`; it does not prevent a second Turn from dispatching the same logical step. B3c must add an explicit durable **v2 logical-step reservation** with unique `(conversation_id, step_id)` for the v2 namespace, bound one-to-one to a Dispatch, or a verified equivalent schema-backed invariant. The Plan row lock serializes conforming Host admission, but is not a substitute for the unique invariant or a full scan of legacy v1 dispatches before reserving. Reservation is never deleted or reused for Gate C; retries will need separately designed attempt identity.

**R3 (P1): revision provenance is not durable across checkpoint.** `PrimaryRouteEvidence.approved_revision_digest` certifies a candidate *at B2b3 commit*, but later mutable `ConversationPlan.snapshot` cannot be reconstructed from a digest. B3c-1 must persist an immutable **revision execution anchor** containing conversation, revision approval Turn ID, SHA-256 of canonical approved candidate, exact initial approved candidate snapshot (bounded, with same access/retention as plan), parent snapshot SHA and frozen origin references. Insert within the original B2b3 revision save transaction for **new** revisions; do **not** retroactively fabricate anchors for old revisions. Alternatively, B3c-only migration can use a separately trusted proof of original candidate at creation; existing approved v2 rows without anchor must fail closed for v2 execution.

**R4 (P1): request SHA must be persisted at original Host admission, not after the fact.** Add nullable `PrimaryRouteEvidence.approved_request_digest` (64-hex format verified) for new revision-2 resume Turn approvals only. Preserve exactly-once/write-once row. Bind `Turn.user_message_id`, exact message text hash, Host decision reason, pre-Turn candidate hash, owner, request_id and candidate anchor. Backfilling a prior allow row from present mutable message content is explicitly forbidden. For B3c-only internal runner, establish a trusted `record_primary_admission` path before any dispatch, even though HTTP exposure belongs to B3d.

**R5 (P1): pristine-only verifier cannot validate progressed v2 snapshots.** B3a `verify_revised_lineage` requires `completed_ids == lineage.preserved_step_ids` and TaskState exact pristine projection; this stops holding after first v2 checkpoint. Create a distinct progressed verifier keyed to R3 anchor that validates immutable v1 completed prefix, original origin refs, consecutive v2 checkpoints and physical Dispatch/Attempt/Receipt; validate TaskState current revision/projection and every intermediate execution in-order. Model-edited lineage, attempted replacement or unknown dispatch fails closed. Never loosen B3a's pristine validator in place.

**R6 (P1): v2 dispatch identity.** Add nullable `PlanToolDispatch.plan_revision`, **set exactly 2** on v2 dispatch, leave historical NULL intact; do not infer that NULL means revision 2. Inherited v1 proof still uses `executions[*].plan_revision == 1` plus physical historical receipts and Host approval, not a fabricated DB backfill. R2 v2 reservation binds exact `dispatch_id`/Turn/step; checkpoint cross-checks both. If choosing a different mechanism, document immutable physical version evidence.

**R7 (P1): post-checkpoint Claim and Turn contract.** B3c keeps **one v2 dispatch+checkpoint per running Turn**. On success, plan + Claim snapshot and TaskState/Attempt/Transition commit atomically, then Turn finalization and Claim release must occur through a defined atomic or explicitly staged Host-owned lifecycle; no stale Claim can authorize a second dispatch. Next Turn is freshly admitted against progressed checkpoint. B3d owns official HTTP multi-turn flow. If no safe Claim release/finalizer is provided in B3c, gate completion must remain internal-only and test future acquisition fails closed.

## H1–H5 decisions

| Hold | Resolution | Release criterion |
|---|---|---|
| H1 immutable anchor | New write-once approved revision anchor persisted at B2b3 candidate save; absence fails closed | migration + in-transaction construction + fingerprint/parent/origin tamper tests |
| H2 version identity | Explicit nullable v2 dispatch version plus unique v2 step reservation (no legacy rewrite) | SQLite & PG database integrity tests |
| H3 progressed proof | Separate anchor-based v2 verifier, never weaken B3a pristine proof | first and second v2 step, mixed v1/v2 Observation tests |
| H4 concurrency | Harmonize all v2 writer lock sequences and add unique reservation; no tool replay | real PG competing workers/receipt/fence/checkpoint/recovery tests |
| H5 execution scope | Exactly one step per Turn; new Turn needs new Host request SHA/Claim | denied second dispatch; guarded new Turn proof |

## Corrected acceptance / sequence

1. **B3c-1**: migration + Host request write-once digest + revision origin anchor on original revision-creation transaction + explicit v2 dispatch revision/reservation schema. Schema migration runs twice safely on SQLite and PostgreSQL. Historical v1 and old unanchored v2 remain readable and non-executable.
2. **B3c-2**: specialized v2 execution boundary: Plan/Claim/Host/Turn/request/anchor/lineage/fence & all relevant Dispatch/Attempt checks in one DB transaction; write unique step reservation + `dispatch_unknown` before any invocation. Use the established, reconciled lock protocol, not the original draft I04 order. Worker competition yields at most one committed dispatch.
3. **B3c-3**: M4-owned observed receipt only; atomic checkpoint: exact versioned dispatch, receipt, Attempt creation, plan+Claim CAS, TaskState+Transition, same v1 historical evidence. Advanced v2 proof supports post-checkpoint plans. No automatic retry of unknown/negative.
4. **B3c-4**: end-to-end internal runner; disconnected official HTTP; real PostgreSQL concurrent and crash boundary tests; exact-head independent review and explicit merge authorization.

## Explicit block conditions

- No code implementing v2 tool invocation until R1–R7 are addressed and independently validated.
- No silent modification of legacy M4, v1 routing, or historical Dispatch revision values.
- No plain Python precheck-only uniqueness for `conversation_id/step_id`.
- No inference of Host approval from an unbound or mutable current request.
- No test claiming an external exactly-once effect; the guarantee is durable **at-most-one dispatch intent** under the B3c no-retry policy.

**Review disposition:** **REVISE DESIGN / HOLD IMPLEMENTATION** until this decision record is folded into a precise implementation specification and accepted. PR #58 remains Draft; no merge or runtime change authorized by this document.
