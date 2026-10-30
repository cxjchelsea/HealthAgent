# S25-02 Final — Unified Mainline Convergence & Legacy Retirement

> Status: **Step 1 / Step 2 — scoped audit and design, pending implementation approval**.
> One work package, one branch, one integration PR; do not create M6/M7/M8.
> Baseline main commit: `8789c0178a0b4962969068e820b302bd4ca3ea59` (PR #26 merge).
> Never claim the final default route has already been switched. Do not modify main or merge without explicit authorization.

## 1. Why this exists

S25-02 M2–M5 delivered evidence-bound semantics, trustworthy captured Turn/checkpoint, guarded Host admission/claim/dispatch, and an opt-in M5 shared route for CREATE/FIRST/RESUME/REVISE/CANCEL. Local real `qwen3:14b` live 11/11 and independent SQLite 34/34 passed; M5 was merged in PR #26 with successful post-merge CI.

However **opt-in does not mean default**. At the source baseline, `Settings` still defaults to `route_admission_mode="legacy"`, `understanding_strategy="direct"`, and `m5_unified_route_enabled=False`. Legacy routing remains reachable in the API, so M5 completion does not itself establish a single system-wide default semantic owner.

## 2. Desired architecture and non-negotiable safety invariants

Logical order:

1. Capture the current user request and the trusted Turn, pending origin and saved-plan checkpoint snapshot.
2. Make **one primary semantic route selection** per eligible new request; keep contextual pending clarification precedence and explicit goal confirmation separate.
3. Use targeted **argument/source/reference/scope extraction and validation** only after primary selection. These are not competing intent classifiers. Refusal/uncertainty must fail closed, never become implied execution permission.
4. Host policy and admission independently decide whether any action may run. LLM proposal is never execution authority.
5. Durable CAS/checkpoint/claim/dispatch, idempotency, recovery and audit are never bypassed by changing semantic routing.
6. Keep an explicit, tested compatibility and rollback mechanism until the new default is proved safe; retire duplicated owners rather than deleting required safety reviewers.

The source of truth for whether a plan exists, its current revision, completion, pending-action origin, user/Turn identity, and prior dispatch remains the Host database/checkpoint—not the model.

## 3. Step 1 — initial source audit (based on main at baseline)

### Configuration reality

`agent/config.py`:
- `route_admission_mode="legacy"` (default)
- `understanding_strategy="direct"` (default)
- `m5_unified_route_enabled=False` (default)
- `m5_first_route_enabled=False` (separate FIRST-only experiment)
- `route_shadow_enabled=False` (diagnostic shadow; not execution authority)

These are **not** changed in this setup commit.

### API entry and competing decisions inventory

`apps/api/main.py` (~1,325 lines at baseline):

| Entry | Current observed count of call sites | Disposition for Step 2 design |
|---|---:|---|
| `propose_new_plan_route` | 1 | **Keep** as M5 primary for no-saved-plan eligible turns |
| `propose_saved_plan_route` | 1 | **Keep** as M5 primary when saved plan exists |
| `review_plan_intent` | 3 | **Audit for retirement/compat-only** when M5 primary is selected; prove no repeated primary semantic decision |
| `review_plan_revision` | 1 | **Keep as bounded parameter extraction**, not alternate intention owner, only for M5 plan_revise |
| `review_plan_cancellation` | 1 | **Audit for compatibility-only** under M5; do not allow overriding M5 'other' |
| `review_plan_creation_reference` | 1 | **Audit for compatibility-only** when M5 has already admitted/rejected a create proposal |
| `review_plan_execution_authorization` | 2 | **Keep as independent execution authorization**, never assume primary route grants execution |
| `review_plan_execution_scope` | 1 | **Keep as independent breadth/scope veto** |
| `check_plan_edit_intent` | 1 | **Audit for compatibility-only**; no second edit intent classifier once M5 decided |
| `explicit_step_binding_conflict` | 1 | **Keep a narrow deterministic conflict fence** if it does not authorize an action |

This inventory is **not a proof** that all paths are independent or incorrect. Before deletion, trace each condition, Host state write and tests. The pending-action path and goal confirmation are trusted pre-turn continuations, **not** fresh primary routing candidates.

### Explicit work left to analyze

- Saved-plan `other` branch must not accidentally run old cancellation/edit intent reviewers as a competing primary owner.
- No-saved-plan `other` must not be silently reclassified as a plan by old `review_plan_intent` under the new default; ordinary tools still need to work.
- Verify that invalid M5 primary model output (schema/UnderstandingError), ambiguous evidence, missing plan, conflicts and revision failures have bounded fail-closed handling rather than HTTP 500, unauthorized tool work or unsafe legacy fallback.
- The high-level route, secondary authorization and general AgentLoop may legitimately use **multiple model HTTP calls**. Target is one **primary route owner**, not one model call total.
- M5 opt-in currently focuses on saved-plan operations and new-plan selection; audit general tool/pending/domain requests before claiming a universal single semantic owner.

## 4. One work package, six steps, one PR

**Step 1 — Audit:** Build complete branch-condition map for new-plan/saved-plan/ordinary/pending/goal confirmation; enumerate state read/write, authority and model-call ownership. Detect unsafe fallback or duplicate semantic owner and record precise file/line references.

**Step 2 — Design freeze:** Produce a before/after route graph, decision ownership table and ordered change list. Mark each old path **keep as safety**, **compat-only** or **remove**. Decide explicit rollback switch and supported deployment defaults, considering fake/local developer mode.

**Step 3 — Implementation:** Consolidate principal route ownership and select the guarded/structured/M5 default for the explicitly supported environment. Preserve an explicit legacy fallback flag only where documented; avoid silently enabling experimental M5 on unsafe modes.

**Step 4 — Compatibility and safety:** Update tests and wiring for ordinary tools, missing/ambiguous source, pending clarification, revision/cancel without authority, repeat request, concurrency, recovery and permission boundaries. Do not weaken Host admission to make CI green.

**Step 5 — Concentrated acceptance:** All CI; real `qwen3:14b` request matrix incl. original 11 scenarios and targeted failures; independent physical SQLite readback; selected PostgreSQL integration/recovery checks where available; verify default path and explicit rollback. Use isolated DB, record exact commit and config in reports. Do not claim production exactly-once from SQLite.

**Step 6 — Independent review and closure:** Exact-HEAD design/code/CI/protection review; one PR; no merge until user explicitly sends `AUTHORIZE MERGE #N`; post-merge main CI and audit. Only then mark **S25-02 CLOSED** within recorded acceptance scope.

## 5. Acceptance gates (must remain externally falsifiable)

- **Architecture:** exactly one primary owner is selected on eligible new Turn; no second conflicting primary classifier downstream. Necessary safety/argument reviewers are clearly differentiated and instrumented.
- **Behavior:** no unauthorized tool call when save-only, revise-only, cancel-only, stale binding or ambiguous source; ordinary and pending behavior remain intact; replay doesn't duplicate effects.
- **State:** Turn identity, plan revision, current checkpoint, dispatch and claim are source bound and durable, including after restart/failure.
- **Defaults:** launch without opt-in variables must exercise the documented chosen default; explicit rollback path is tested.
- **Evidence:** exact commit + runtime config, live scenario outcomes, actual persisted DB inspection and CI status are correlated; no fake/mock test is called real-model live.
- **Governance:** all required checks complete on exact PR HEAD, no protection bypass, Standard Merge Commit only after authorization.

## 6. Stop conditions / exclusions

Stop implementation and request design reconsideration if audit discovers that route owner and Host executor authority are coupled or a true default switchover would bypass an existing safety fence. Do not start S25-03, add new tools, invent unrelated agent capabilities, migrate clinical logic, or change production auth as part of this work package.

**Current decision:** work package and preliminary inventory established. Step 1 needs full branch-condition audit; Step 2 design requires review before Step 3 production code changes.
