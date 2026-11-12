# S25-07 Gate B2b3 — official HTTP revision CAS (2026-11-12)

**Base:** main@`1b60321863fd0e96cd62ba727943ccbb87635f91` after B2b2 PR #54.

## Authorization boundary

- Alembic revision `0022_revision_approval_digest` adds nullable `PrimaryRouteEvidence.approved_revision_digest`. A historical B2b2 proposal-only admission has NULL; never synthesize a permit for it.
- `record_primary_admission` persists the SHA-256 of the deterministic B1 candidate (canonical sorted JSON) only after a genuine `allow/dependency_revision_candidate` Host outcome. Candidate is rebuilt from the current DB-bound user message and the exact pre-Turn snapshot. The digest is written once with the existing admission evidence.
- `commit_dependency_revision_in_session` re-checks all existing B2a locks / trusted completed prefix and deterministically recompiles the candidate. It requires the **exact immutable approved digest** before CAS; any NULL or differing candidate is denied.
- The official HTTP Primary route does not call tools. Its initially staged `dependency_revision_admitted_not_saved` internal result is converted **inside the terminal Turn transaction** to either `dependency_revision_saved_pending_execution` with Plan revision 2 or a fail-closed partial reason. A SAVEPOINT isolates B2a Plan/TaskState/Transition side effects; Turn/assistant response status are committed together, never claiming a saved plan after failure.
- All revision 2 plans keep executor `dependency-plan-read-replan-v1` deliberately non-executable. No B3 resume or Gate C retry.

## Required tests

- B2a SQLite / real PostgreSQL atomic CAS (existing fixtures now insert the approved SHA).
- Official HTTP successful revision and exact evidence fingerprint, with no Dispatch or execution.
- Host-approved candidate A replaced by different, individually user-grounded candidate B: must deny and roll back all plan/task changes.
- Host admission races a different writer's Plan update: must deny stale CAS and preserve newer Plan.
- Idempotence of Alembic 0022 and existing API baseline regressions.

## Scope limitations / upcoming work

- A valid semantic proposal may not yet succeed with a live model; Qwen live evidence belongs to Gate D. This implementation is tested with deterministic model fixtures.
- B3 must independently implement cross-revision provenance and resume under explicit Host execution controls. No automatic execution.
- Final independent review and explicit user authorization are still required for merge.
