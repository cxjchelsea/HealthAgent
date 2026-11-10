# S25-06 Slice D — Recovery authority and lifecycle contract

**Status:** implementation contract for Draft PR #39; feature **OFF** by default.
This is not an approved production recovery runbook.

## Trust boundary

- The API and model MUST NOT call `reconcile_dependency_orphan`. It remains an internal-only service, never registered as HTTP, MCP, tool, or skill.
- Two independent authority checks are mandatory: an operator IAM/RBAC decision explicitly scoped to operator, owner, conversation and Turn; and independent proof that the exact executing worker has terminated or been fenced. Caller-supplied `operator_authorized=True` and `worker_terminated_confirmed=True` do **not** substitute for either verifier.
- The verifier functions in tests are simulated, including second-process tests: they **do not** prove that a real deployment can authenticate an operator or identify a dead worker. Do not configure the recovery entrypoint until backed by a trusted operator identity system and a worker-ownership/lease or orchestration-fencing mechanism. Current Claim schema stores a Turn but **no worker identity/epoch**; time elapsed since `created_at` is insufficient proof of termination.
- The operator must inspect the exact Plan and Dispatch/Attempt evidence checksums and confirm the specific stranded Turn. Reconcile revalidates all evidence in one database transaction with Plan → Claim → Dispatch → Attempt locks. Mismatched evidence or worker liveness is **deny**, not best-effort recovery.

## State decisions

| Situation | Permitted action | Forbidden action |
| --- | --- | --- |
| Claim belongs to a live or not-proven-dead worker | Inspect only | Release Claim, change Turn |
| No Dispatch, worker independently confirmed dead | Mark old Turn partial + audit + release Claim | Pretend execution completed |
| Only checkpointed Dispatch(es), worker confirmed dead | Preserve completed prefix; mark old Turn partial + audit + release Claim | Replay completed step |
| `dispatch_unknown` | Preserve Dispatch and evidence; old Turn partial + audit + release Claim only after proven death | Retry or infer success/failure |
| `observed` (positive receipt, no checkpoint) | Preserve receipt, require operator investigation; optional eventual separately proven disposition | Auto-checkpoint or auto-retry |
| `observed_failure` | Preserve failure and require explicit disposition | Reclassify to success or remove fence |
| Contradictory/incomplete Dispatch/Attempt/Receipt | Refuse reconciliation | Guess from remaining fields |

All three unresolved Dispatch states remain in `UNRESOLVED_DISPATCH_STATUSES`, and the Host rejects new tool Dispatches for the conversation even after the prior Turn is marked partial. Claim release is **not** an execution clearance. Normal conversation turns may resume once the stranded running Turn is closed, subject to other application guards.

## Still required before production authorization

1. Install a real operator authorization provider with scoped identities, least privilege, auditability and denied-access tests.
2. Add durable worker instance ownership/fencing epoch or use a trusted orchestrator to prove the *specific* execution holder stopped, including network partitions and delayed workers. Never use a bare timeout or PID from a remote host as proof.
3. Design a separate evidence-based disposition for each unresolved tool state and its necessary external adapter guarantees, including double-worker races and replay prevention. Do not automatically clear unresolved rows.
4. Independently verify real FastAPI multi-worker HTTP restart and authorization behavior in the target deployment topology, not just subprocess database tests.
5. Keep S25-06 default OFF; do not merge or enter Slice E until the remaining gates have been explicitly reviewed.
