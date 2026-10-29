# S25-02 Final — Step 2 统一主链目标架构与整改设计（设计冻结候选）

> **Status: DESIGN READY FOR OWNER REVIEW — implementation NOT authorized by this document.**
> Baseline: `main@8789c0178a0b4962969068e820b302bd4ca3ea59`; work branch `refactor/s25-02-final-mainline-convergence`; Step 1 audit: `S25-02-FINAL-Step1-主链完整审计.md`.
> Scope: **one work package, six steps, one branch, one final PR**. No separate M6/M7/M8.
> Invariants: Host decides authority; model never grants execution; pending origin, CAS/claim/dispatch/recovery and durable Turn are preserved.

## 1. Design decision and exact meaning of a single mainline

**Single primary semantic decision** means the current eligible ordinary Turn has one authoritative high-level route selection, with no later model reviewer permitted to replace that route with another plan lifecycle intent. It does **not** mean only one model HTTP call: argument binding, scope, action safety, planning and general Understanding may still call models in strictly narrower roles.

Two existing M5 owners are **context-exclusive branches of one primary route contract**, not two decisions for the same Turn:
- No saved plan: `propose_new_plan_route` -> `plan_create | other`.
- Saved plan: `propose_saved_plan_route` -> `plan_execute_first | plan_resume | plan_revise | plan_cancel | other`.

Trusted continuations (pending tool argument answer and goal confirmation) have precedence and **do not invoke a new primary classifier**. A captured source quote is evidence, not authority. Security reviewers can **veto**, not add/swap route intent.

**Important limitation**: this final work package consolidates the **plan-lifecycle primary route and ordinary/clarification handoff**, not a fully universal agent-domain intent router. Do not claim all general AgentLoop / goal-understanding decisions have been abolished.

## 2. Before/after branch-condition map

### Before: main baseline

```text
Turn creation -> preturn pending + plan capture
  ├─ optional M5 new-plan primary (guarded+structured+flag, no plan/no pending)
  ├─ optional M5 saved-plan primary (guarded+structured+flag, saved plan/no pending)
  ├─ independently reachable legacy cancel / FIRST-only / plan-intent / edit-intent
  ├─ structured Understanding + clarification/policy/goal decisions
  ├─ plan admission, independent execution authorization + scope and Host fences
  ├─ AgentLoop or terminal no-op
  └─ terminal CAS/claim/dispatch/checkpoint / audit
Default: legacy + direct + M5 disabled
```

### Target: default, explicit routing owner and bounded continuations

```text
Owned request + request-id replay guard + durable running Turn + preturn facts
  ├─ goal_confirmation present -> bounded Host goal confirmation (no fresh primary)
  ├─ pending_action/clarification_id present -> verified continuation
  │    └─ classify supplied answer / validate original tool args / limited resume
  │       (no fresh plan route and no fallback into plan execution)
  └─ new eligible request -> ONE M5 primary route, selected from saved-plan existence
       ├─ plan_create -> exact source + step schema/Host create admission -> save-only
       ├─ plan_execute_first/plan_resume -> scoped execution veto + Host M4 admission
       │    -> exact-snapshot claim + verified dispatch + checkpoint
       ├─ plan_revise -> targeted old/new args extraction + pending-only binding
       │    -> versioned CAS; never execute
       ├─ plan_cancel -> Host cancel admission + CAS delete; never execute
       ├─ other -> ordinary Understanding/policy/AgentLoop, **not legacy replan**
       └─ ambiguous/invalid/refused -> bounded failure, no plan mutation / tool dispatch
  -> one terminal transaction -> diagnostics/shadow (post-commit, opt-in)
```

### Critical routing contract

`other` is an **affirmative terminal decision about plan-intent ownership**, not an authorization or instruction to run a tool. Ordinary tool execution still requires its existing independent policy and tool schema checks. An unresolved saved plan must not be consumed by ordinary AgentLoop as an implicit plan control. If the original request explicitly says SAVE ONLY / DON'T EXECUTE or refers to a missing plan, apply **deny-only constraints**; a model's `other` result cannot override those constraints.

A primary refusal must **never** enter Legacy or generic AgentLoop to obtain permission for the same plan operation. Invalid schema or provider outage = terminal/503 with no action; quote absent/nonunique and unsafe binding = bounded clarification/partial with no action.

## 3. Exact owner disposition and deletion rules

| Entry / component | Target owner class | Design disposition | Important constraint |
|---|---|---|---|
| `propose_new_plan_route` | PRIMARY | KEEP, adapt as no-plan branch | One call on eligible Turn; no speculative execution |
| `propose_saved_plan_route` | PRIMARY | KEEP, adapt as saved-plan branch | One call on eligible Turn; Host-bound Turn/checkpoint |
| `review_plan_intent` | LEGACY-ONLY | Move behind explicit legacy mode (including calls currently at `main.py:452,466,756`) | Must never reselect plan lifecycle route after M5 `other` |
| `review_plan_cancellation` | LEGACY-ONLY | Remove from M5 path, retain only explicit rollback | M5 `other` cannot be overridden by separate cancel classifier |
| `check_plan_edit_intent` | LEGACY-ONLY | Remove from M5 path; retain if rollback truly needed | Uncertain REVISE -> clarification; never semantic reclassification |
| `review_plan_creation_reference` | LEGACY-ONLY | Never call when M5 already made CREATE decision | Host source binding remains mandatory |
| `propose_first_route` / `m5_first_route_enabled` | RETIRE/LEGACY-ONLY | Disallow FIRST-only owner when default M5 runs; document temporary rollback if needed | No simultaneous FIRST and unified primary |
| `review_missing_plan_reference` | BOUNDED DENY/LEGACY-ONLY | Under M5 no-plan, use Host-grounded missing-plan gate without granting route; legacy reviewer only in explicit rollback | No execute without prior plan |
| `review_plan_revision` / `apply_plan_revision` | SCOPED EXTRACTION + HOST BINDING | KEEP, invoke solely after primary `plan_revise` in M5 | Explicit step, old/new exact source, unchanged steps preserved |
| `explicit_step_binding_conflict` | DENY-ONLY HOST FENCE | KEEP if monotonic; cannot synthesize REVISE | May refuse early; preserve full-sentence semantics |
| `review_plan_execution_authorization` | INDEPENDENT VETO | KEEP | A primary FIRST/RESUME is never sufficient authority |
| `review_plan_execution_scope` | INDEPENDENT VETO | KEEP | Do not widen FIRST to RESUME without verified scope |
| `admit_guarded_plan_create/cancel/execution` | HOST AUTHORIZATION | KEEP | Must fail closed on stale/ambiguous state |
| `saved_plan_execution_guard` / CAS / `PlanDispatchJournal` | HOST DURABLE EXECUTION | KEEP | Exact pre-turn snapshot, no replay of unknown outcome |
| `UnderstandingService.understand` | GENERAL GOAL/CONTEXT/PENDING | KEEP WITH OWNERSHIP LIMIT | No downstream plan primary reclassification; no tool authority bypass |
| `decide_execution_policy_detail` / `reconcile_execution_policy` | POLICY / VETO | KEEP WITH OWNERSHIP LIMIT | Does not convert M5 plan `other` into saved-plan run |
| `classify_pending_reply` | SOURCE-BOUND CONTINUATION | KEEP | Original pending authority only; never fresh plan/create execution |
| `evaluate_shadow` | POST-COMMIT DIAGNOSTIC | KEEP OPT-IN | Read-only; never grants execution or changes HTTP outcome |
| `agent_decider` / injected `execution_policy` | EXPLICIT TEST/INTEGRATION INJECTION | KEEP, tested separately | Never imply injected-mode results certify live default |

**No blind deletion** of `agent/plan_intent.py`, the structured Understanding layer or legacy tests until call-site and rollback behavior have been shown equivalent. Legacy code is controlled by explicit compatibility mode, not automatically tried after a M5 refusal.

## 4. Configuration decision: default and rollback

### 4.1 Proposed target defaults, pending acceptance

```python
route_admission_mode = "guarded"
understanding_strategy = "structured"
m5_unified_route_enabled = True
m5_first_route_enabled = False
route_shadow_enabled = False
```

These values are **proposals for Step 3**, not changes in this design. The existing `model_provider="fake"` default complicates a blind global flip: fake/testing/development injection must be analyzed and fixed or explicitly selected, not silently sent through a real model prompt protocol that fake cannot reliably satisfy.

### 4.2 Explicit backward compatibility profile

Environment settings can select `legacy` / `direct` / `m5_unified_route_enabled=False` for rollback **before process startup**. Do not permit runtime per-request toggle based on model output, and do not silently fall back to legacy when M5 fails. Mixed inconsistent flags should be **rejected at application startup** (or normalized only through a documented, tested profile resolution), not treated as authorization for a hybrid path.

Design preference: a single validated routing-profile resolver (e.g. `unified` vs `legacy`), with old variable aliases retained only for transparent compatibility during migration. If this requires schema/API change, Step 3 should minimize churn and provide tests for every supported combination.

**Deployment gate:** no actual default flip until fake provider, test injection and direct-mode consumers are accounted for and real-model post-default smoke passes; revert via explicit configuration if acceptance fails.

### 4.3 Observable configuration

`GET /model` (already returns current M4/M5 fields) should expose the effective active routing profile, structured vs direct mode, and source of default/override **without disclosing secrets**. Live and physical test artifacts must include exact runtime profile, model/provider and HEAD SHA (do not trust self-reported SHA without external git verification).

## 5. Implementation sequence within Step 3 and Step 4

1. Introduce a small **effective route profile contract** at startup; reject incompatible settings, preserve explicit rollback, test fake and injected modes. Change defaults only after compatibility tests establish behavior.
2. Restructure `send_agent_in_conversation` around three mutually exclusive states: Host confirmation, Host pending continuation, new eligible Turn. For new Turn, assign a **single primary route result once**; downstream can consume it but cannot call another primary classifier.
3. Make `review_plan_intent`, `review_plan_cancellation`, `check_plan_edit_intent`, and creation reference reviewer strictly compatibility-path components. Preserve narrow syntax/source refusal fences and safety reviews.
4. Keep existing `UnderstandingService` and action policy for ordinary requests, clarify that they **cannot mint plan controls** or cancel/revise saved plans in unified mode.
5. For the M5 `other` handoff, explicitly suppress downstream plan-admission classifiers. Preserve an action policy veto and prevent implicit plan dispatch.
6. Fail closed on invalid primary JSON, malformed quotes, multiple source matches, absent/stale plan references, contradictory revisions, incomplete authorization/scope. Do not broad-catch errors into ordinary execution.
7. Do **not** rewrite M4 durable claim/dispatch/CAS logic except for a strictly necessary compatibility bug with dedicated old/new behavior tests.
8. Update rollout documentation and migration steps; keep a single integration PR for Step 6.

## 6. Test and acceptance design (Step 4/5)

### A. Routing ownership and defaults
- Starting with **no routing overrides**, `GET /model` shows the documented effective profile; eligible Turn uses exactly one model category `primary`, `plan_intent` category = 0; safety calls may be >0.
- Explicit rollback profile exercises old paths with unchanged safety gates; conflicting/half-enabled settings fail startup, not at dispatch.
- `other` request may use ordinary tools via policy but cannot create/save/revise/cancel/execute a plan. Test no-plan ordinary, saved-plan ordinary and unrelated general goal action.

### B. All 11 existing real-model scenarios
- CREATE_A, FIRST, RESUME, REPLAY, CREATE_B, REVISE, CREATE_C, CANCEL, ORDINARY, PENDING, PENDING_ANSWER; require 11/11 PASS with fresh isolated DB.
- M5 CREATE must save only; FIRST only first; RESUME remaining; REVISE only one pending step without tool call; CANCEL no tool; replay returns 409 with zero new execution.

### C. Negative / ambiguity / spoof tests
- Primary model malformed JSON / invalid schema / timeout -> fixed failure contract, no plan/side-effect mutation, never legacy fallback.
- Quote not in original / quote occurs more than once / model-supplied wrong offset -> fail closed or bound Host-derived exact unique text; never trust model positions.
- User asks to run a missing prior plan; explicit save-only conflicting with tool request; duplicate client request; wrong step/old arg, completed-step revision; malformed / stale checkpoint.
- Fake pending clarifications, answer belonging to another Turn / old conversation, mixed task write permission, cancellation authority omitted, resume scope not confirmed.
- Saved-plan `other` and model denial must never be executed via a second plan reviewer.
- Concurrent Turn requests and stale CAS must not overwrite new plan or cause duplicate dispatch; unknown external outcome must remain manual reconciliation.

### D. Physical/recovery gates
- SQLite: current live and physical readback plus additional denied-turn DB invariants, no active claims, no duplicate dispatch, no ghost checkpoint.
- PostgreSQL: CI schema migration, two-worker collision, kill/restart/unknown-outcome checks; real Qwen+PG physical E2E if operationally available, otherwise explicitly mark **NOT COVERED**.
- Post-default check must correlate real HTTP server, provider, model, runtime routing profile, live report, DB path and exact git HEAD.
- Standard pytest/CI, package checks and integration suites remain mandatory; tests asserting legacy defaults must be migrated or explicitly run under legacy profile.

## 7. Traceability to Step 1 findings

| Step 1 ID | Step 2 decision | Acceptance owner |
|---|---|---|
| F01 | Effective profile contract, guarded+structured+M5 intended default after test gate | config / startup / default-mode E2E |
| F02 | M5 `other` strictly excludes downstream plan-intent reinterpretation | router contract + ordinary tools |
| F03 | Legacy primary reviewers reachable only via explicit rollback profile | call-category assertions + legacy tests |
| F04 | Uniform invalid/refused semantic output contract; no fallback | negative provider tests |
| F05 | Missing saved reference = fail closed / plan_not_found | no-plan negative E2E |
| F06 | Validate effective default vs exactly tested config | real model live + artifact verification |
| F07 | Count `primary` per eligible Turn, not total model HTTP calls | metrics + trace |
| F08 | Retain M4 CAS, claim, dispatch and reconciliation | SQLite/PostgreSQL crash/concurrency |

## 8. Open design questions requiring Step 3 validation (not assumptions)

1. Does `fake` provider respond adequately to new primary JSON schemas? If not, define a **test-only explicit legacy fixture** or deterministic fake route adapter that cannot leak into live deployment; never let accidental fake fallback pick legacy behavior silently.
2. Can a user-facing deployment safely enable unified defaults if model credentials are absent? `/readyz` currently reports DB readiness and configured-provider flag separately; define readiness policy, do not invent production auth.
3. Do any tests rely on injected deciders or `direct` defaults? Inventory before changing `Settings` defaults and migrate intentionally.
4. Does `UnderstandingService` perform a second *plan-lifecycle* decision for `other`, or only general goal/action interpretation? Step 3 tests must prove negative side effects under both saved-plan and no-plan cases.
5. Which M5 quote/binding refusals should return HTTP 200 partial vs 503 provider error? Fix contract before rollout; never retry as authorization.
6. Should FIRST-only feature flag be retained for rollout rollback, or retired after profile-based legacy support? Must not become a third active primary owner.

## 9. Step 2 exit criteria and owner review request

**Status: Step 2 design documented, awaiting owner architecture approval.** No production changes, default switch, PR or merge in this step.

Proceed to implementation only after agreeing to:
- One primary plan-lifecycle semantic owner on eligible Turn (exclusive no-plan/saved-plan branch), bounded `other` and no legacy fallback after refusal.
- Guarded Host veto and durable claims unchanged, even if additional HTTP model calls remain.
- Explicit startup profile/default migration, fake/test and pending safety, and rollback.
- Full 11/11 plus extended negative/default/physical acceptance before Step 6.

The acceptance outcome of M5 PR #26 is **baseline evidence**, not evidence that this new design or default migration already passes.
