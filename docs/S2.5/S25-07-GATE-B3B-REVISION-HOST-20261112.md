# S25-07 Gate B3b — internal Host RESUME preparation & historical Observation binding

**Base:** `main@ad054ebe6cfa4b65da580cf992e8b12c2a1db597` (B3a merged).

## Scope, not an execution release

The opt-in `S25_07_REVISION_RESUME_PREPARE_ENABLED=false` defaults OFF. B3b is an **internal-only** Host/binder capability. Official HTTP does NOT set `PrimaryHostFacts.revision_resume_prepare_enabled` and therefore revision-2 requests cannot enter the S25-06 AgentLoop or Dispatch. B3c and B3d are prerequisites for any runtime execution.

- **Same Primary semantic owner.** An explicit `plan_resume/remaining_steps/read_only` proposal may be Host-admitted for revision-2 only with a separately enabled `PrimaryHostFacts.revision_resume_prepare_enabled`, matching the exact pre-Turn plan and current user quote. `plan_execute_first` and stale/partial proposals are rejected. The Host emits `allow/dependency_revision_resume_prepare_only`, **not** `primary_v1_plan_execution_candidate`.
- **Separate inert binding.** `bind_revised_plan_read` requires the guarded active settings flag, exact owner/current running Turn/request ID and User Message, existing matching durable `PlanExecutionClaim`, matching immutable Primary admission evidence and original snapshot digest. No Claim is created and nothing is written.
- **Physical proof first.** The binding invokes B3a's `verify_revised_lineage`, which validates the full pristine revision-2 candidate, Host-approved SHA, original revision-1 Dispatch/Attempt/receipt and S25-03 TaskState; only the **first pending step** may be prepared.
- **Version-aware source.** For a pending `mcp_reference_lookup`, bind the plan's exact pinned literal. For pending `text_stats`, derive text exclusively from the original revision-1, checkpointed MCP definition and validate its Attempt/receipt/argument digest; the model cannot supply free-form observation. The resulting `PreparedRevisedRead` is a distinct type from the S25-06 `PreparedDependencyRead`, so v1 Dispatch APIs cannot inadvertently accept it.
- **No authority laundering.** A read-only prepared object is not a permit and cannot be cached/replayed as durable dispatch authority. B3c must independently verify lineage, current Claim, Host evidence, worker fence, source observation, argument digest and no conflicting Dispatch *within the atomic dispatch boundary*. In particular it must support v1 historical `plan_revision=1` without reclassifying them as v2 attempts.
- **Pristine checkpoint only.** After the first v2 Checkpoint, the candidate SHA and TaskState revision will change. B3c must anchor the original approval separately and verify new v2 evidence without dropping source checks. Gate C retries are not part of B3b.

## Tests

The existing B3a SQLite and actual PostgreSQL fixtures are extended with pre-owned `PlanExecutionClaim`, running Turn, Primary Evidence and deterministic Host proposal. Cases: no completed prefix (literal MCP), one completed revision-1 MCP prefix (text_stats observation), disabled flag/execute-first/wrong step, owner/request/Claim/Plan mismatch, tampered source receipt, Attempt or Host evidence. A successful bind is verified to perform **zero** Dispatch, Attempt or Plan writes.

## Remaining implementation

B3c implements locked M4 dispatch and checkpoint, preserving original revision-1 evidence; B3d connects official HTTP and multi-process/post-crash physical validation; Gate C is separate. Do not merge without independent review and explicit authorization.
