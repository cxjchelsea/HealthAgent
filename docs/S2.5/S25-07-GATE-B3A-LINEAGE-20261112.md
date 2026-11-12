# S25-07 Gate B3a — revision 2 lineage verification contract (2026-11-12)

**Base:** main@`e9df790fcd4201fe9daec87250cdc34efb5e408c` after PR #55.

This is the **first, read-only slice** of Gate B3, not the full cross-revision executor.

## Why a separate slice is necessary

S25-06 `_step_for_read`, Observation Resolver, Dispatch and checkpoint are revision-1 only. Existing execution records correctly carry `plan_revision=1` even after B2b3 creates revision 2. Blindly removing `record.plan_revision == checkpoint.revision` would accept unsupported or fabricated history.

## B3a contract

`verify_revised_lineage(session_factory, owner_id, conversation_id, expected_raw)`:

- Requires an exact persisted revision 2 snapshot with `dependency-plan-read-replan-v1` executor tag and the bounded, prefix-completed DAG. Each step remains a pinned, schema-valid read; text_stats can use only a source observation.
- Requires **one** approved revision Candidate SHA-256 in immutable `PrimaryRouteEvidence`, a completed/partial revision Turn, owner identity, and `AgentTaskState(plan_revision=2)` with the exact step status projection and saved transition.
- Checks lineage origin_refs equality to each inherited `executions[*]` record, preserving original `plan_revision=1`. No newly invented v2 execution history is accepted.
- For **each completed origin**, checks the actual checkpointed `PlanToolDispatch`, successful `AgentAttempt`, original Host-authorized execution Turn, tool/version, argument digest, projection and tool-specific original argument/receipt content. text_stats lineage binds to the exact source MCP definition; no model text is treated as evidence.
- Rejects additional unknown/failed/pending Dispatch and stale snapshot. Returns only an immutable read-only proof, **not** a Claim or tool permit.

This is a best-effort snapshot inspection and is **not** an atomic execution authorization. B3b must repeat lineage validation while holding the proper Plan/Claim/Dispatch locks at dispatch and checkpoint boundaries. The original S25-06 execution routes remain revision-1 only. No fallback is introduced, and `revision=2` remains non-executable.

## B3 remaining steps

B3b: new revision-aware Host prepare/binding and source observation resolution, enforcing same exact approved prefix under M4 Claim.

B3c: fenced dispatch and checkpoint with parent-record `plan_revision=1` inheritance, revision-2 new evidence, and no duplicate Dispatch or retry.

B3d: official Primary/HTTP RESUME and real PostgreSQL multi-process/crash/fence/recovery adversarial verification. Gate C retries are separate.

**CI:** new tests check no-prefix and physical inherited-prefix positives on SQLite/PG, forged approved digest, wrong user, Plan CAS mismatch, fake source/digest, extra Dispatch, task projection, and confirmation that old executor still rejects v2.

Keep Draft; do not merge without independent review and explicit user authorization.
