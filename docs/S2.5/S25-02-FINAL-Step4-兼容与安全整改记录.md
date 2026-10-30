# S25-02 Final — Step 4 兼容性与安全整改记录（2026-10-30）

> **Step 4: CODE + AUTOMATED SAFETY REGRESSION PASSED**. Step 5 real Qwen3/default-profile physical acceptance **NOT PERFORMED**. No PR or merge.
>
> Branch: `refactor/s25-02-final-mainline-convergence`; code HEAD prior to this documentation commit: `07649417685cbf161b90c03ecec9641ead4d65c2`.
> Full CI: https://github.com/cxjchelsea/HealthAgent/actions/runs/40980799173 (**SUCCESS**).
> Baseline main remains PR #26 merge: `8789c0178a0b4962969068e820b302bd4ca3ea59`.

## 1. Independent review and actual correction

Step 3 introduced `routing_profile=auto|unified|legacy`. In an **explicit auto compatibility override** path, a half-configured M5 could previously remain at `legacy + direct + m5_enabled`, appearing configured but never activating guarded M5. This is a startup observability and safety-of-operation defect, not a demonstrated unauthorized tool execution.

Fixed `agent/config.py` validation:
- In `auto`, if explicit `m5_unified_route_enabled=True`, require `route_admission_mode=guarded`, `understanding_strategy=structured` and `m5_first_route_enabled=False`; otherwise startup raises `inconsistent_unified_route_flags`.
- If explicit `m5_first_route_enabled=True`, require guarded + structured; otherwise reject `inconsistent_first_route_flags`.
- Keep **supported M4 compatibility** (`guarded + structured + M5 false`), explicit FIRST-only guarded compatibility, and fake-provider offline defaults.
- Explicit `routing_profile=unified|legacy` and contradictory historical environment flags continue to be rejected during validation.
- This does **not** change Host execution authorization, CAS, dispatch, claims or recovery.

## 2. Regression scenarios added and hardened

`tests/test_s25_02_final_step4_negative.py` added:
- Saved-plan M5 `other` with Legacy plan/revision/cancel functions replaced with fail-fast stubs: no semantic reclassification; checkpoint remains unchanged.
- Two refusal scenarios: no-plan CREATE and saved-plan operation with fabricated model source quote -> bounded no-action refusal, no persisted creation/mutation.
- Two invalid-primary schema scenarios: `UnderstandingError` produces HTTP 503 with fixed code, no ordinary AgentLoop fallback or saved-plan mutation.
- Explicit routing profile environment conflict -> settings validation failure.
- Six half-enabled configuration permutations -> startup failure rather than deceptive silent legacy fallback.
- Positive M4 compatibility and FIRST-only compatibility profile assertions.

`tests/test_s25_02_m5_unified_pending.py` strengthened:
- Pending tool-argument continuation both **with** and **without** saved plan must not call fresh M5 primary **or any legacy plan/revision/cancel model semantic owner**.
- Original trusted pending operation resumes, and saved plan remains unchanged.

### Test-harness limitation distinguished from product failure

The first new saved-plan `other` test used `model_provider=fake` and expected ordinary NativeToolDecision to complete HTTP 200. This is not guaranteed: the fake provider has no model-backed ordinary tool-decision implementation and correctly returned HTTP 503 `unsupported_provider`. The fixture was corrected to accept that fake-only outcome while requiring no legacy classifier invocation and no saved-plan mutation. **Real ordinary-turn success must be independently verified with Qwen3 during Step 5**, and fake-provider 503 is not presented as passing real chat behavior.

## 3. Evidence

- Initial new regression CI `40980502805` = FAIL (one fake ordinary-provider test assertion; 764 passed, 3 skipped). Verified from job log, then corrected.
- Subsequent `40980679109`, `40980729842` = SUCCESS with incremental test changes.
- **Exact latest code HEAD** `07649417685cbf161b90c03ecec9641ead4d65c2` = CI `40980799173`, **SUCCESS**. Includes full pytest and existing PostgreSQL CI checks.
- No main branch mutation; no user merge authorization requested or implied.

## 4. Step 4 decisions and remaining Step 5 gates

**Step 4 code hardening and compatibility regression = COMPLETE**, subject to Step 5 real-model validation.

Step 5 must run **fresh real Qwen3** with:
1. Startup `ROUTING_PROFILE=auto` (or explicitly unified), `MODEL_PROVIDER=ollama_proxy`; **remove all four old routing overrides** to verify actual new default. Check `GET /model` shows `routing_profile=unified`, `route_admission_mode=guarded`, `understanding_strategy=structured`, `m5_unified_route_enabled=true`.
2. Repeat original 11-case full live matrix (plan CRUD, replay, ordinary calculation, pending, clarification) and physical SQLite checks using the **same isolated database**.
3. Additional negative real-model probes: no-plan `plan_missing`, ambiguous/missing quote (model-unsafe reply under test), saved-plan `other`, explicit save-only denial, failed revision and execution-scope veto. Record separately from 11-case score.
4. PostgreSQL migration/crash/two-worker CI evidence and, when available, real-Qwen PostgreSQL physical E2E. Do not call a SQLite PASS a PostgreSQL E2E.
5. Record exact running code HEAD and effective runtime configuration. If default-mode live fails, **do not** merge or mark S25-02 CLOSED. Investigate without disabling Host admission.
6. Independently review exact HEAD, full CI, schema/config compatibility and current main before a single final PR. Merge only on explicit `AUTHORIZE MERGE #N`.

**One work package, one branch, one final PR.** No M6/M7/M8 and no premature broad refactor or new agent capability.
