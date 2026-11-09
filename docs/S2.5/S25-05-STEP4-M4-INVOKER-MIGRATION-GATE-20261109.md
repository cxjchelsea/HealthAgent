# S25-05 Step 4 — M4 / saved-plan Invoker migration gate (2026-11-09)

Status: **CONTRACT AND REGRESSION TESTS ONLY**. No M4 or saved-plan execution migration is authorized or implemented by this change.

## Verified call sites at parent HEAD 3cdcd9a

- Ordinary AgentLoop tools: `agent/loop.py` in `_tools` first writes a prepared Attempt, invokes an opt-in read via `OrdinaryReadInvoker` or the legacy executor, then finalizes the Attempt.
- Saved bound-plan: `AgentLoop.run` with `plan_control in {"execute_first", "resume"}` and a fully bound pending checkpoint. After a snapshot guard: `dispatch_journal.begin(step_id, tool, args)`, then existing `execute_tool`, then `record_receipt`, then `PlanLedger.observe`.
- Plan failure recovery: only attempts an alternative when candidate validation succeeds and the snapshot guard still passes. When a dispatch journal exists, it does **not** attempt a second unjournaled call.
- Primary route proposal is not an execution permit; PlanLedger only admits an explicit bounded list of read tools. Registry opt-in flags grant **ordinary** reads, not saved-plan execution.

## Required invariant before any saved-plan adapter

1. Host-authorized plan scope and current durable snapshot guard must be checked before `begin`; never rely on an ordinary Host read grant to execute a saved step.
2. A prepared journal record exists before the external effect, and receipt binding precedes marking a step completed. A crash between these points is an unknown outcome, not a safe auto-retry.
3. Tool names, arguments and their digest must remain bound to the authorized step. Any recovered tool version must be explicitly and durably bound, not inferred from the current registry version.
4. A failing guard must produce no journal entry or tool execution. A journaled failure must not launch an unjournaled recovery tool.
5. Write tools, Pending confirmation and owner identity remain Host-owned. Registry/Invoker may validate or dispatch but cannot grant execution rights or mutate the CAS state machine.

## Tests added with this review

- Saved-plan M4 order: begin -> existing executor -> receipt; observed step success and no ordinary invoker.
- Snapshot guard denial: no M4 begin or tool call, step stays pending.

These are narrow scripted regression tests, **not** PostgreSQL concurrency/crash proof. Full CI and the preexisting PostgreSQL matrix are needed on the exact new HEAD.

## Later staged implementation proposal

1. First build a **non-executing** read-only PlanInvocation/authority model and inspect the transactional dispatch services. Explicitly separate plan grants from ordinary grants.
2. Add opt-in, version-pinned saved-plan invocation behind its own Host-issued permit; retain the existing journal begin/receipt/CAS sequencing.
3. Test crash before and after dispatch, receipt duplication, step-revision mismatch, owner boundary and live Qwen. Add a DB migration for tool version metadata with unknown for legacy records; do not retroactively mark old attempts as a current version.
4. Only after independent review enable the saved-plan invoker by default. Do not migrate any write or Pending path in this work package.

PR #33 remains Draft until separate user authorization.
