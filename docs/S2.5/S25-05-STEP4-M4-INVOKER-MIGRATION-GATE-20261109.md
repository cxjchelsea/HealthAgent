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

## Non-executing PlanInvocation slice (2026-11-09)

`agent/plan_invocation.py` now provides `inspect_plan_invocations(checkpoint, control)` as a **pure structural inspection** of the current saved-plan checkpoint. It returns frozen descriptors (plan revision, step ID, control, tool name, canonical arguments digest, tool_version=None). It validates **all** pending tools/arguments, including later steps when `execute_first` was requested, before returning only the first binding.

This object is NOT a Host-issued grant. It has no owner, Turn CAS, journal claim, API authorization or dispatch capability, and it does not invoke any tools. Historical checkpoint tool versions remain unknown, not guessed as the current Registry version. Ordinary `ReadInvocation` remains a distinct type. Existing `AgentLoop` plan and M4 pathways were not rewired.

Tests assert snapshot binding/digests, no version invention, rejection of writes, invalid args, bad controls, exhausted plans, and invalid later steps. Exact-head CI is required to establish compatibility.

**Next gate:** independent review of the Host Turn/owner/CAS + M4 journal service to design a genuinely Host-issued versioned plan execution permit, separate from this descriptive data model. Do not pass `PlanInvocation` directly into `OrdinaryReadInvoker`.

## Host-bound plan execution descriptor (2026-11-09)

Added `agent/host_plan_execution_binding.py` with `bind_host_plan_execution` for a **non-executing** Host-admitted plan descriptor. Inputs include a `PrimaryHostDecision`, trustworthy owner/conversation/Turn/request identifiers from the API Host, and the exact serialized pre-Turn checkpoint. It requires `allow/read_only` with matching `plan_execute_first` or `plan_resume` control and known Host reason, checks the parsed raw snapshot against the checkpoint, and produces a SHA-256 digest of the **raw snapshot** plus validated step binding digests. Historical tool versions remain unknown. It rejects mismatched route/control, missing identity, malformed snapshot and invalid step arguments.

**Security boundary:** The Python dataclass is *not a bearer token, cryptographic signature, authenticated identity or self-sufficient capability*. Any in-process caller could construct it. The only permissible future construction point is the trusted API Host after owner-scoped Turn creation and deterministic admission; the existing durable snapshot claim, Turn CAS, M4 dispatch journal and checkpoint checks are still required at actual execution. Do not pass an object from clients, model output or a restored checkpoint as an already-approved permit.

The builder is not yet called by the API or AgentLoop. This slice changes no production routing or effects; next gate is mapping the exact production Host handoff and DB ownership/CAS before a separately gated integration.
