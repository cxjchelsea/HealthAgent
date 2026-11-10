# S25-06 Slice E2 — Independent closeout and pre-merge gates (2026-11-10)

## Baseline and verdict

- Repository: cxjchelsea/HealthAgent
- E2 stacked review HEAD: `8ef3d24c6597df28972666ed86fd3fc9bc165550`, based on E1 `fd339d8ecd2c416c20de2659529579966e5f4aee`; main at review `64fe592df87b994bf73da210963c2e89561f8067`.
- PR #41 (E1) and PR #42 (E2): open Draft, **not merged**.
- Exact E2 HEAD CI 42786523456 SUCCESS: pytest 1477 passed / 5 skipped, targeted PostgreSQL 6 passed / 2 skipped (according to PR run).
- Independent review submitted to PR #42, review ID 5839548986.
- **Disposition: conditional E2 pass in controlled-provider and default-OFF scope only. This document is not merge authorization.**

## Verified implementation boundaries

1. Existing single Primary proposes a versioned `dependency-plan-v1` payload behind an explicit opt-in. Host independently checks guarded Turn, plan/Pending absence, request quote and bounded plan schema. There is no second semantic classifier.
2. Create path records Primary evidence before any effect. Creating a plan invokes no tool, Claim or Fence; new-format plans do not flow into legacy PlanLedger.
3. The terminal `Session.begin()` transaction persists `ConversationPlan` with `compare_and_swap_in_session(expected_raw=None)` under a `begin_nested()` SAVEPOINT and finalizes assistant Message and Turn in the same transaction.
4. A competing plan writer yields `dependency_plan_create_conflict`, a non-success partial response and preservation of the existing plan. Injected post-flush exception rolls back the new plan, attempts cleanup of a running Turn and returns 503.
5. HTTP tests cover create → FIRST → RESUME, disabled mode, Host rejection, a simulated out-of-band competing writer, post-flush rollback and controlled Primary schema parsing. PostgreSQL subprocess test covers only **one-running-Turn exclusion**: a second HTTP Turn receives 409 before Primary.

## Transaction-outcome uncertainty policy

- **Definite absence:** only when a fresh trusted database read confirms no saved plan for the conversation; a client-side 503 alone is not proof of absence.
- **Definite presence:** a trusted read finds the versioned plan; compare its recorded identity/revision/content with the intended request and immutable Primary Turn evidence before calling the original create successful.
- **In-doubt:** timeout, lost connection near COMMIT, cleanup failure or inconsistent Turn/plan evidence. Do **not** retry creating a new plan automatically, delete an existing plan automatically, replay FIRST/RESUME, or report confirmed success. Surface a stable in-doubt state to the operator and reconcile from the durable Plan, Turn, Message, and PrimaryRouteEvidence records after reconnecting.
- Existing `dependency_plan_create_commit_failed` cleanup is **best effort**. It is not a proof of commit outcome and does not implement an operator reconciliation API. A future reconciliation design must be separate from user-facing automatic replay.
- For a confirmed no-plan failed Turn, a **new explicit user request** may initiate a new create Turn, subject to the normal Host guards; do not reuse the failed Turn's execution authority.

## Acceptance evidence boundaries

| Gate | Current evidence | Interpretation |
| --- | --- | --- |
| Single Primary / Host / default-OFF | E1/E2 deterministic regressions | Pass in controlled scope |
| Create→FIRST→RESUME | isolated SQLite HTTP / trusted local MCP | Pass; not live Qwen proof |
| Conflicting plan insertion | injected writer between pre-Turn snapshot and terminal CAS | Pass for simulated race |
| Terminal rollback | injected post-flush exception | Pass for tested exception boundary |
| PostgreSQL multi-process | one-running-Turn 409 gate | Pass for admission serialization, **not** simultaneous final CAS race |
| Unknown COMMIT response | code review and documented operator policy | Not physically proven; no automatic recovery guarantee |
| Real model generation | no real `ollama_proxy/qwen3:14b` E2 create proof | Blocked on E3 |
| Production IAM, worker-death proof and exactly-once external effects | out of scope | Must not be claimed |

## E2 pre-merge checklist

- [x] Review exact HEAD `8ef3d24c` and tests; independent comment posted on PR #42.
- [x] Record the uncertain-commit policy and distinguish simulated plan CAS race from PostgreSQL single-running-Turn exclusion.
- [x] Confirm previous exact-code CI and PostgreSQL scope are green.
- [ ] Recheck this documentation commit's exact-head CI if applicable.
- [ ] Obtain explicit user authorization to merge E1 PR #41; validate its latest HEAD and merge first.
- [ ] Rebase or otherwise restack E2 on the resulting main without duplicate E1 commits, rerun exact-head CI and independent diff check.
- [ ] Obtain separate explicit user authorization to merge E2 PR #42.
- [ ] Keep `s25_06_dependency_plan_enabled` default OFF. Actual Qwen natural-language and recovery acceptance belongs to E3.

## Recommended E3 execution gate

Using a new conversation and actual `ollama_proxy/qwen3:14b`, test the real schema-bound Primary output for natural-language dependency-plan creation. Assert a durable `dependency-plan-v1` snapshot, zero tools/Dispatch/Claim/Fence during creation, FIRST executing only a ready predecessor step, RESUME consuming checkpointed trusted Observation, and exactly the expected durable Receipts/Attempts. Include schema refusals, source spoofing, invalid graph or tool version, missing plan, default-OFF and conflict. Run PostgreSQL crash/restart and operator in-doubt scenarios as independent physical cases. A fake-model PASS does not satisfy these gates.

## Explicit exclusions

This closeout neither changes runtime behavior nor certifies production safety, dynamic replanning (S25-07), untrusted MCP, clinical permissions, or external exactly-once guarantees.
