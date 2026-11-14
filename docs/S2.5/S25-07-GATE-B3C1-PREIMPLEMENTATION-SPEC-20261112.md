# S25-07 Gate B3c-1 — exact preimplementation specification (rev 1, 2026-11-12)

**Source of truth:** `main@b9b9ed1481b2ff00f3bce56551b26cc38277c53d`, PR #58 independent review `1db9f0a`. This is a **design artifact**; no database or execution code has been changed here.  
**Decision:** B3c-1 may implement only durable schema + authorization/origin evidence and tests. No invocation, no Dispatch, no new HTTP routing, no automatic retry. Require independent review before opening B3c-2.

## 0. Scope & precise existing facts

- Current last migration is `0022_revision_approval_digest`; next migration **0023** with `down_revision="0022_revision_approval_digest"`.
- `PrimaryRouteEvidence` is keyed by Turn, has nullable `approved_revision_digest` for `plan_revise`, but no `approved_request_digest`. `record_primary_admission` separately commits write-once Host evidence.
- `commit_dependency_revision_in_session` runs inside the caller's B2b3 revision-save transaction, independently recompiles from DB Message and locked parent and checks `approved_revision_digest`; it currently updates plan and task only, without immutable candidate snapshot anchor.
- `PlanToolDispatch` has only `UNIQUE(turn_id,step_id)` (not conversation-wide uniqueness), no revision column; `AgentAttempt.id == Dispatch.id` for checkpointed steps.
- `revision_fingerprint` SHA is canonical JSON with `sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False`. Exact persisted `ConversationPlan.snapshot` is separately serialized with `ensure_ascii=False` and default separators. Both representations must not be confused.
- `verify_revised_lineage` is intentionally pristine-only: completed prefix is *exactly* `lineage.preserved_step_ids`. Subsequent v2 completion needs a different validator.
- `record_receipt` locks Dispatch before Fence, while v1 dispatch and checkpoint have different order. B3c-1 must not silently rewire v1. B3c-2/3 must define dedicated v2 writers or proven lock coordination.

## 1. Migration 0023 — additive and fail closed

Extend `primary_route_evidence` with:

```
approved_request_digest VARCHAR(64) NULL
```

This field is meaningful **only** for `allow / plan_resume / dependency_revision_resume_prepare_only` of the same Turn. New writer validates 64 lowercase hex chars; missing/NULL never authorizes v2. Other Primary routes leave NULL.

Add table `dependency_revision_anchors` (one immutable origin per conversation for bounded revision=2):

```
conversation_id        VARCHAR(40) PRIMARY KEY REFERENCES conversations(id)
revision_turn_id       VARCHAR(40) NOT NULL UNIQUE REFERENCES turns(id)
approved_candidate_json TEXT NOT NULL
approved_digest        VARCHAR(64) NOT NULL
parent_snapshot_sha256 VARCHAR(64) NOT NULL
origin_refs_json       TEXT NOT NULL
created_at             TIMESTAMP WITH TIME ZONE NOT NULL
```

Use the existing ORM `DateTime(timezone=True)` dialect mapping, not a PostgreSQL-only migration type. `revision_turn_id` points to the **B2b3 plan_revise** Turn, **never** a later resume Turn. `approved_candidate_json` stores a bounded accepted *plan*, not full model output or user prompt, and follows the same access-control/retention policy as `ConversationPlan.snapshot`. Fingerprint calculated from `json.loads(approved_candidate_json)` MUST equal `approved_digest`; `approved_digest` MUST equal immutable `PrimaryRouteEvidence.approved_revision_digest` for `revision_turn_id`; parent SHA and `origin_refs_json` MUST equal the frozen plan lineage fields. Anchor row is insert-only (no update/delete in application execution path). DB row immutability is an application + privilege requirement; describe any DB-level trigger protections explicitly if introduced.

Add nullable `plan_revision INTEGER` to `plan_tool_dispatches`: historical NULL remains NULL, no backfill; B3c v2 writer will set exact integer 2. NULL **is not** proof of revision 1: inherited historical v1 requires independent B3a physical verification. A new v2 dispatch with NULL is always rejected.

Add `dependency_v2_step_reservations`:

```
conversation_id VARCHAR(40) NOT NULL REFERENCES conversations(id)
step_id         INTEGER NOT NULL
dispatch_id     VARCHAR(40) NOT NULL UNIQUE REFERENCES plan_tool_dispatches(id)
turn_id         VARCHAR(40) NOT NULL REFERENCES turns(id)
created_at      TIMESTAMP WITH TIME ZONE NOT NULL
PRIMARY KEY(conversation_id, step_id)
```

This table is **not populated in B3c-1** (no Dispatch). In B3c-2 it is inserted atomically alongside v2 Dispatch, and never deleted/reused in Gate B3c. No global `UNIQUE(conversation_id,step_id)` on existing Dispatch table, because it would retrofit legacy M4 behavior. Gate C needs a separately reviewed attempt/step identity contract.

SQLite and PostgreSQL migrations must use portable Alembic constructs, upgrade 0022 -> 0023, and pass repeated `migrate()`/upgrade-to-head, clean install, schema visibility, unique/FK and nullable legacy-row assertions. Avoid asserting that a raw standalone `upgrade()` invocation twice succeeds; Alembic version ledger makes repeated upgrade-to-head safe.

## 2. B2b3 revision-save atomic origin anchor

The *only* creation path for a new anchor is `commit_dependency_revision_in_session`, after (i) checking exact owner, running revision Turn, DB user message, immutable Primary allow evidence; (ii) re-compiling the candidate from locked parent & DB text; (iii) checking original revision fingerprint, completed evidence, no active Claim/Fence/unknown pending Dispatch; and (iv) verifying TaskState CAS conditions.

Within **the same database transaction** as replacing `ConversationPlan.snapshot`, updating `AgentTaskState`, adding `AgentStateTransition` and terminal Turn/message save by B2b3, insert exactly one `DependencyRevisionAnchor` row. If it conflicts, the *whole revision save* must roll back. Never create an anchor in `record_primary_admission`: Host evidence alone does not prove that the plan was committed.

Calculate:
- `approved_candidate_json = json.dumps(verified.candidate, ensure_ascii=False)` (exact snapshot to be committed).
- `approved_digest = revision_fingerprint(verified.candidate)`.
- `parent_snapshot_sha256 = sha256(expected_raw UTF-8)`.
- `origin_refs_json = json.dumps(verified.candidate["lineage"]["origin_refs"], ensure_ascii=False)`.

One active revision-2 candidate per conversation is already the bounded B1/B2 contract. An existing older v2 plan without anchor, or conflicting pre-existing anchor, must **fail closed** for v2 execution; do not reconstruct a purportedly original candidate from today's mutable plan or replay a model to generate an anchor. In this B3c-1 implementation, no historic v2 backfill is permitted. New revision saves after migration produce the new anchor.

The anchor is *not execution authority*: it proves original saved candidate identity, which later v2 execution combines with a fresh per-resume Host permit and live Claim/Fence/physical history.

## 3. Resume request authorization persistence

Extend `record_primary_admission` narrowly: when decision is `allow/dependency_revision_resume_prepare_only`, require:
- proposal route exactly `plan_resume`, `execution_scope=remaining_steps`, `effect_intent=read_only`, proper exact quote binding from current DB User Message, and exact `Turn.user_message_id`;
- DB user message exists, same conversation, role `user`, status `completed`, content byte-identical to the Host-reviewed request, and Turn is `running`;
- decision's `approved_request_digest` is 64 lowercase hex and equals `sha256(DB Message.content UTF-8)`;
- `preturn_snapshot_digest` is for the exact current saved plan and matches the `checkpoint_raw` reviewed by Host;
- current plan is revision-2 bounded executor and matches an independently verifiable approved anchor, with no mismatched source history.

Write the request digest into the **same insert-only PrimaryRouteEvidence row** as the Host route/disposition/snapshot hash. If any check fails, do not write an allowed row. A second write for the same Turn is always rejected; no later mutation/backfill even if message content changes.

This design is a necessary narrow extension of `record_primary_admission` and **must not** make other route cases require a revision anchor. The official HTTP path continues not to set `revision_resume_prepare_enabled`; B3c-1 authorizes no actual dispatch.

The current B3b binder's in-memory `PrimaryHostDecision.approved_request_digest` check remains in place; B3c-2 will additionally read this durable field under execution locks, not replace it with a potentially stale Python decision.

## 4. Atomic transaction and lock contract (revised H4)

Do **not** copy the original PR #58 draft I04 order uncritically. The current v1 interactions include `Plan -> Claim -> Fence` for initial dispatch, `Plan -> Claim -> Dispatch -> Fence` for checkpoint, and `Dispatch -> Fence` in `PlanDispatchJournal.record_receipt`. That inconsistency is a **known cross-writer deadlock risk**, not something a new v2 function can fix by declaration.

B3c-1 performs **no** M4 Dispatch/Receipt/Checkpoint writes. Its production write transactions are:
- revision-save: current locked Plan -> Claim -> Fence -> historical Dispatch / Attempt -> TaskState; add Anchor in that transaction without introducing a second lock-order inversion;
- admission recording: existing write-once Primary evidence per Turn, with new durable message fingerprint and verified candidate anchor; no tool effect.

For B3c-2/3, require a **dedicated v2 receipt writer and v2 checkpoint/recovery protocol**, all using the same resource order as the v2 dispatch path. Treat an attempted use of legacy M4 receipt/recovery against a v2 dispatch as denied until an explicit compatibility audit. Lock-order/cycle analysis must enumerate Plan, Claim, Fence, Dispatch, Attempt, TaskState and the immutable Anchor; complete real PostgreSQL two-worker + receipt + checkpoint + revoke tests before opening the v2 internal runner. On DB deadlocks, a bounded transaction retry is permissible **only before external invocation** and **only without repeating an already committed Dispatch intent**.

## 5. New data-model contract

B3c-1 adds ORM entities matching migration 0023:
- `DependencyRevisionAnchor` immutable saved candidate origin.
- `DependencyV2StepReservation` future no-replay step slot.
- nullable `PrimaryRouteEvidence.approved_request_digest`.
- nullable `PlanToolDispatch.plan_revision`.

No new free-text prompt/evidence/raw tool response fields. Sensitive tool receipts remain bounded in the existing M4 journal; the anchor stores approved plan JSON already present in saved plan, not user utterances.

All changes to existing public routes, `agent/loop.py`, `PlanLedger`, S25-06 tool invoker, existing recovery behavior, or fake-model policy are **out of scope**.

## 6. Targeted acceptance (must pass before B3c-1 review)

1. New revision-save transaction creates one anchor whose candidate JSON, SHA, parent digest, revision Turn and origin_refs exactly agree with saved approved plan and B2b3 Host evidence.
2. CAS conflict / wrong owner / forged parent / forged approval / failed TaskState update / duplicate anchor insertion -> full rollback: no new Plan, Task, Transition, Turn terminalization or anchor.
3. Existing unanchored v2 plan remains readable; no retroactive anchor or Dispatch authorization. Existing v1 workflow unaffected.
4. Allowed internal B3b-style resume evidence persists matching request digest on the exact Turn and user message; source mismatch, text mutation, wrong Turn ID, missing user_message_id and wrong proposal scope deny with no allowed row.
5. No raw user message stored in `PrimaryRouteEvidence`; other routes' evidence unchanged and request digest NULL.
6. `plan_revision` historical NULL remains; future v2 reservation PK and Dispatch FK uniqueness enforced by DB in both SQLite (FK enabled) and PostgreSQL.
7. Alembic 0023 upgrade-to-head twice and clean database upgrade; full old CI + targeted SQLite and physical PostgreSQL tests.
8. All new B3c flags remain default off; no new invocation/Dispatch/HTTP path.
9. No deleting or mutating original revision1 Dispatch/Attempt/Receipt during anchor creation.
10. Ensure the current official revision HTTP transaction actually owns the B2b3 commit; don't introduce a separately committed anchor after the HTTP response.

## 7. Implementation acceptance decision

**Eligible next work:** B3c-1 schema/evidence implementation on a new feature branch from a known `main` SHA, optionally after merging the independently approved documentation PR #58. This file does NOT authorize a merge or runtime tool invocation.

**B3c-2 HOLD:** a v2 dispatch must not be introduced until migration/anchor/request evidence are physically tested and the cross-writer lock protocol is concretely implemented and independently reviewed.
