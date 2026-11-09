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

## API Host admission integration (2026-11-09)

In `apps/api/main.py` Primary saved-plan first/resume now requires the existing guarded-plan admission **and** the extra `host_plan_binding_matches` structural check before the established M4 reconciliation and AgentLoop dispatch. This new check uses trusted `uid`, conversation ID, Turn ID, request ID, raw pre-Turn snapshot and existing in-memory checkpoint. A mismatch reuses `primary_v1_plan_admission_denied`, without invoking a tool.

The check is supplementary, not an authorization token. All durable conditions remain with original owner-scoped conversation lookup, Turn creation, `saved_plan_execution_guard`, `PlanDispatchJournal`, and checkpoint CAS. No signed capability, tool-version database migration, write/ordinary permission expansion, or legacy route rewrite was introduced.

New tests include fail-closed identity/snapshot/scope matching and an HTTP denial case confirming zero PlanToolDispatch rows. Exact new HEAD CI plus independent positive-path and PostgreSQL execution tests are still required. PR #33 stays Draft.

## Durable tool-version provenance slice — 2026-11-09

Alembic revision `0019_tool_versions` adds nullable `tool_version` to `agent_attempts` and `plan_tool_dispatches`. **NULL means unknown**, including all pre-migration rows; no version backfill occurs.

For ordinary opt-in Registry reads only, AgentLoop now places the Host-bound `host_read_versions[name]` on the *prepared* Attempt before tool invocation. `AttemptEvidenceStore.begin` persists it and `finish` requires the identical nullable version for its prepared-to-terminal transition. Non-opted legacy tools remain `NULL`; `make_attempt` remains source-compatible with a default unknown version.

The M4 saved-plan executor and `PlanDispatchJournal` continue to write `NULL` tool version until a **separate trusted plan-level version bind** exists. This is intentional: the current Registry's version must not be retroactively assigned to checkpointed old-plan work. The new column alone **does not** grant version-pinned saved-plan replay, does not authorize unknown-version replays, and does not change M4 recovery logic.

New tests cover owner-scoped attempt persistence, legacy unknown versions, successful versioned preparation/completion, denial of mismatched version completion, Registry HTTP version recording, and saved-plan dispatch staying unknown. CI's Alembic head assertion is upgraded to `0019_tool_versions`.

Remaining acceptance: exact-head GitHub CI including PostgreSQL migration, M4 crash/concurrency and targeted SQLite checks; then design Host-signed/persisted plan tool version with unknown-record quarantine before any M4 ToolInvoker migration.

## Prospective saved-plan legacy-executor version pin (2026-11-09)

`agent/plan_tool_versions.py` declares `LEGACY_PLAN_EXECUTOR_VERSION = "legacy-plan-v1"`. This is a **separate, explicit version of the existing `agent.tools.execute_tool` saved-plan adapter**, not the opt-in `READ_REGISTRY` version `1.0.0`. Any semantic change to its underlying implementation must bump the contract version. Because Python code itself is not cryptographically signed, this is an application-maintained version contract, not a code integrity attestation.

Guarded API Host newly authored checkpoints now get `tool_version` on every plan step **before the Turn's durable checkpoint CAS**. `PlanLedger.restore` preserves these step fields through subsequent checkpoints and revisions. Host binding checks all pending steps for consistent version information before M4; `AgentLoop.run` independently fences mismatched or mixed versions prior to any journal begin. The M4 journal accepts a trusted pinned version as a dedicated keyword argument and records it on the pre-dispatch row; the final checkpoint binding verifies it. The same code path retains `NULL` for unversioned historical checkpoints.

**Historical policy:** Entirely unversioned old checkpoints remain supported by the legacy executor in this compatibility slice, with `tool_version=NULL` evidence. They are *not* certified version-safe; automatic execution of old plans after a future incompatible executor upgrade will need a separate explicit migration or quarantine policy. Mixed step versions, tampered pinned versions and explicit `NULL` downgrades fail closed. A version marker on a new plan does not alone issue permission: existing source authorization, Host M4 admission, Turn claim, guard, journal and CAS still apply.

**Scope:** No `OrdinaryReadInvoker` migration, no write-tool authorization expansion and no claim of exactly-once external effect. Version checks are strict for version-pinned plans but not retroactive for existing legacy plans.

Acceptance gates: exact-head CI, first/resume HTTP with newly created Host-pinned checkpoints, PostgreSQL dispatch version persistence and crash/recovery tests, plus historical data review. PR remains Draft.

## Version-upgrade and recovery hardening (2026-11-09)

The pre-versioned plan compatibility is now explicitly limited to the original
`UNVERSIONED_LEGACY_COMPATIBLE_VERSION = "legacy-plan-v1"`. As soon as the
running `LEGACY_PLAN_EXECUTOR_VERSION` is bumped, unversioned checkpoints
are **fenced before tool dispatch** (both Host admission and AgentLoop).
They must be manually reviewed/recreated; the system does not silently pin
them to a new version. A version-pinned old checkpoint also fails closed
on an incompatible version bump.

In the *offline* M4 `reconcile_verified_receipts` path, a persisted observed
dispatch must carry the **exact same nullable tool version** as the checkpoint
step before it may mark a step completed. The normal M4
`checkpoint_in_session` applies the same exact equality, including rejecting
versionless receipts for version-pinned steps. Quarantine still never replays.

PostgreSQL subprocess crash verification now exercises both legacy unversioned
and pinned `legacy-plan-v1` dispatches, with/without a receipt, and a
tampered-version challenge before offline reconciliation. This is not
external-effect exactly-once proof. Tests also simulate an executor version
upgrade and verify zero M4 dispatch.

No automatic historical version backfill, permission change, or M4-to-Registry
dispatch migration is part of this slice. CI result for exact HEAD remains
the next acceptance gate.

## M4 → OrdinaryReadInvoker integration review / negative contract (2026-11-09)

**Decision: NO direct migration on this commit.** The existing `OrdinaryReadInvoker` is authorized only for the Host's ordinary read route. A saved plan is separately admitted by the Primary Host and M4 execution claim. An `OrdinaryReadInvoker` call with the plan's `legacy-plan-v1` version is rejected by the Registry, whose tool definitions use `1.0.0`. Replacing that expected version with `1.0.0` would silently change the contract and is therefore NOT a valid migration.

Four distinctions must be resolved before enabling migration:

1. **Host authority:** A plan must receive its own durable Turn/owner/checkpoint-bound grant; ordinary `approved_read_grants` must continue to return no permissions for `plan_execute_first` or `plan_resume`. Neither a model proposal nor `PlanInvocation` is a bearer token.
2. **Execution transaction:** Keep `saved_plan_execution_guard`/claim before each dispatch, `PlanDispatchJournal.begin` before any tool call, receipt afterward, and final exact-snapshot CAS/checkpoint binding. No unjournaled recovery retry. A reusable Invoker must not independently dispatch a plan without these guards.
3. **Adapter semantics:** Legacy MCP uses `StdioMCPClient.call`; the Registry adapter uses `call_trusted_reference` (trusted handshake/catalog validation). The two are not behavioral no-ops. Tool error contracts, runtime scoping and result metadata also need explicit equivalence or a versioned migration.
4. **Version and crash upgrade:** The historical `legacy-plan-v1` identifier is NOT equivalent to Registry `1.0.0`. If changing adapters, explicitly mint a new execution contract for NEW plans; old and unknown-version plans are never implicitly re-pinned or automatically replayed after a semantic upgrade. Retain PostgreSQL crash/reconciliation tests.

The new `tests/test_s25_05_m4_invoker_migration_gate.py` codifies the current negative contract: incompatible versions reject, saved-plan routes do not inherit ordinary grants, MCP adapters differ, and a matching version alone does not grant permission. These are **migration blockers by design**, not an invitation to relax the checks.

**Next implementation slice:** design a separate `PlanReadInvoker` with explicit plan-scoped, Host-bound input and injected durable dispatch lifecycle. Review a staged route-specific canary only after parity tests for the whole supported plan-tool set and PostgreSQL interrupted-dispatch recovery. Keep the existing M4 executor active meanwhile.

No production source or M4 execution order was modified by this audit.
