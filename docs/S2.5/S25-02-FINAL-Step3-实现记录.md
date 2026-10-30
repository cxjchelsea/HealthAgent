# S25-02 Final — Step 3 Implementation Record (2026-10-29)

> Status: **IMPLEMENTED / automated CI verified**; Step 4 compatibility/security hardening and Step 5 real-model/physical acceptance **NOT YET COMPLETE**.
> Branch: `refactor/s25-02-final-mainline-convergence`. Main remains at `8789c0178a0b4962969068e820b302bd4ca3ea59`.
> Exact code-tested HEAD before this documentation commit: `f8b584b84211d8bb58e74b9becbe35e105a84ba1`.
> CI run: https://github.com/cxjchelsea/HealthAgent/actions/runs/40851964708 (SUCCESS).

## Implemented code

### A. Routing profile and default migration — `agent/config.py`

Introduced `routing_profile: auto | unified | legacy`. Startup validation resolves:

- `auto` + real configured provider (**non-`fake`**) + no explicit historical routing overrides -> `guarded + structured + m5_unified_route_enabled=True + m5_first_route_enabled=False`.
- `auto` + default `fake` provider -> maintain legacy/direct/disabled offline fixture behavior.
- `auto` + any explicit legacy/M4 routing flag -> preserve historical combination as **explicit compatibility** instead of silently changing an operator's override.
- `routing_profile=unified` -> enforce the complete guarded/structured/M5-enabled/FIRST-only-disabled tuple and reject contradictory explicitly supplied flags at settings validation.
- `routing_profile=legacy` -> enforce legacy/direct/M5-disabled/FIRST-only-disabled tuple and reject contradictory explicitly supplied flags.

`Settings.effective_routing_profile` returns `unified`, `legacy`, or `compatibility`; `GET /model` now exposes effective and requested profiles and the actual understanding mode (without secrets).

**Important truth:** `Settings()` with the built-in **fake provider** still intentionally yields legacy. The *real-provider no-override launch* defaults to unified. Step 4/5 must verify both offline fixture compatibility and a real provider no-override API start.

Rollback (before process start) can set `ROUTING_PROFILE=legacy` **with no contradictory legacy routing variables**. To force unified, set `ROUTING_PROFILE=unified` and remove conflicting legacy overrides. Do not use runtime per-request toggles.

### B. Missing saved-plan route within one primary — `agent/m5_unified_route.py` / `apps/api/main.py`

No-plan M5 source-bound primary now recognizes `plan_missing` in addition to `plan_create` and `other`. `plan_missing` requires a Host-located unique exact current-request quote and **zero steps**. Its `PlanRequest(intent="resume", steps=[])` is a narrowly scoped internal **denial signal** used by the existing Host admission resolver, **not authority to dispatch**. Host verifies absence of saved plan and terminates `plan_not_found`.

When the unified primary already selected this missing-plan denial, API no longer invokes an independent legacy missing-plan classifier to rediscover the intent.

### C. Pending continuation precedence — `apps/api/main.py`

For resolved unified profile with trusted pending argument or clarification source, the legacy cancellation, plan-intent, plan-revision and late generic plan-classifier branches are not entered. Pending continues using the original verified argument/clarification authority. Goal confirmation remains separate.

The existing M5 `other` semantics are reused: the `PlanRequest(intent="other")` from a no-plan primary prevents legacy reclassification during late plan admission; a saved-plan `other` is converted into the same non-plan decision and does not trigger a standalone cancellation or revision owner.

### D. Safety unchanged

No authorization grant was added for model-selected plan intents. Existing guarded Host admission, revision old-argument binding, scope authorization, CAS/checkpoint/claim, dispatch journal, replay and recovery remain in place. Explicit compatibility flags continue to work, but must not be mistaken for the unified default.

## Tests and automated evidence

Added `tests/test_s25_02_final_routing_profile.py`:
- real-provider no-override unified profile
- default fake provider legacy fixture mode
- explicit legacy rollback and explicit unified test profile
- reject contradictory explicit settings at model validation
- deliberate historical compatibility settings classification
- no-plan missing-reference API returns `plan_not_found` without a second legacy plan/reference classifier
- unique current-request evidence required for `plan_missing`.

Latest code HEAD CI run `40851964708`: SUCCESS. Do not infer real Qwen3 runtime accuracy from mock/CI checks alone.

## Step 4/5 unresolved acceptance and cautions

1. Verify live Qwen3 with **no routing overrides**; capture `GET /model` profile, real provider, exact server HEAD, 11-case matrix + expanded negatives.
2. Run independent SQLite physical readback against the **same** API DB, plus PG recovery/concurrency in the configured environment.
3. Exercise pending continuation with a saved plan at the same time, stale Turn and source spoof, invalid JSON/quote, contradictory revision/scope and no-plan fallback.
4. Confirm rollout env precedence and fake/test injection behavior; if any legacy test or production setting requires contradictory profile flags, it must fail startup rather than silently loosen Host policy.
5. Run an independent review of modified lines before moving to final acceptance.
6. Documentation is not executable evidence; use exact final commit CI after all edits, and do not merge without explicit owner authorization.

**Step 3 code implementation is complete for the approved design scope; Step 4/5 validation remains separate.**
