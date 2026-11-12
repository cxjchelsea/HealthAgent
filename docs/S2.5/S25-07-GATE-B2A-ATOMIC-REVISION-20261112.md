# S25-07 Gate B2a — Locked revision CAS (2026-11-12)

**Base:** `main@3600e466cae0221d50defc296fa8ebdbde4d41af`, B1 merged as PR #51.

## Exact delivery

`services/dependency_revision_commit.py::commit_dependency_revision_in_session` adds a database transaction boundary, **not a new API or tool**. It requires a previously written immutable `PrimaryRouteEvidence` with `plan_revise / allow / dependency_revision_candidate`. That Host decision is NOT produced by production today: current Primary Host still explicitly rejects dependency plan revision. Until B2b, the service is non-reachable from the official HTTP route.

Inside one transaction: Plan FOR UPDATE -> Claim FOR UPDATE -> Fence FOR UPDATE -> Dispatch FOR UPDATE -> Attempt/TaskState; checks owner, active Turn, stored user message, immutable preturn digest, no Claim, no active Fence, no pending-step Dispatch (including `failure_finalized` and unknown), exact old snapshot, all completed-step durable Receipt/Attempt/arguments projection; **additionally validates actual historical arguments against the original saved-step input bindings, re-checks `text_stats` source dispatch / definition / counts, requires a separate previously Host-admitted execution Turn, and denies a completed prefix without its old TaskState projection**; recompiles the untrusted proposal from its proposed pending steps with the DB-stored user message, compares the entire derived candidate and lineage, then updates Plan, AgentTaskState plan_revision and task transition atomically. The new snapshot has the distinct, **non-executable** `dependency-plan-read-replan-v1` runtime tag.

A new `text_stats` literal cannot be committed in B2a because old S25-06 executes only trusted observation references. The B1 candidate language is broader than today's runtime capabilities; no false claims of executability are permitted.

## Explicit non-goals and outstanding checks

- **B2b** must wire actual Primary semantic intent, version-aware Host admission, user-message/Turn binding, and atomic HTTP terminal result. The current DB `Message` schema has no direct `Turn.user_message_id` FK, so `user_message_id` is an input verified for same conversation/role/content but **not yet cryptographically linked to the specific Turn**. Therefore the B2a method MUST NOT be exposed as an independent public endpoint or presented as a complete authorization path.
- **B3** must validate revision 2 lineage, executed source evidence and capability bindings across all callers before any RESUME. Never enable this revised executor in S25-06's `_step_for_read` simply by relaxing revision equality.
- **Gate C** must use a separately reviewed attempt/migration protocol to handle `failure_finalized` re-dispatch. Current B2a rejects any dispatched pending step, so failed-step recovery is NOT yet enabled.
- Candidate revision 2 is single-generation-only; revision 3+ is not supported.
- Trust boundaries in the present code rely on immutable DB Primary admission, stored user message, and prior DB evidence; full route-origin coupling is a later required deliverable.

## Verification

`tests/test_s25_07_gate_b2a_atomic_revision.py` exercises SQLite and **real PostgreSQL when TEST_POSTGRES_URL is configured in CI**: one winner for competing revisions, exact revision CAS, active Claim/Fence denial, unknown/finalized pending Dispatch denial, tampered lineage, mismatched source text and absent/denied Host permission. It does not call a model, tools, or new HTTP route. Existing regressions run in CI.

Keep PR Draft until independent review and merge authorization.
