# S25-02 Step 5 / M2 — Internal Cross-Regression Closure (2026-10-27)

## Decision

**INTERNAL TECHNICAL REGRESSION: PASS. FORMAL M2 GATE: HOLD. M3 CUTOVER: NOT AUTHORIZED. MERGE: NOT AUTHORIZED.**

This is an evidence record, not an independent acceptance attestation, integration approval, or a change to the semantic classifier. Preserve the tested code SHA below; a documentation commit changes branch HEAD but not the code SHA under test.

## Frozen code and CI

- Tested code SHA: `cffd9fcba819cc5f89fa4a4c652d1d2acb4f56e4`
- Candidate branch: `fix/s25-02-m2-unified-plan-action-20261027`
- Model: `ollama_proxy` / `qwen3:14b` (user's local execution)
- GitHub CI for exact tested SHA: success; **574 passed, 3 skipped**.
- CI: https://github.com/cxjchelsea/HealthAgent/actions/runs/40467694314
- Source reports were supplied by the user from local real-Qwen execution and checked in the review conversation. These local JSON artifacts are not committed here; their SHA references and results are recorded below.

## Cross-regression results at the exact tested code SHA

| Cohort | Manifest SHA-256 | Repetition 1 | Repetition 2 | Provenance / scope |
| --- | --- | ---: | ---: | --- |
| Legacy AI development 84 | `418408790481ff9c1e77542b9da7dd8cd740d2d7921a62016d7e5071b9129ed6` | 84/84 | 84/84 | Known AI-generated development set; not a third-party blind cohort |
| Earlier new validation 72 | `fce18a9d39ec41d2675a84af09579f61deffec79dbec245f7146560b741125a4` | 72/72 | 72/72 | Assistant-authored validation, not independently attested |
| Simulated reviewer 72 | `9ca74e8cc3b2f597fe581771999742236a9a32e48e0b28bbfda5fd1c6cbc33f5` | 72/72 | 72/72 | Assistant-authored simulated reviewer, **not** an actual external reviewer |
| **Combined** | Three distinct fixed cohorts | **228/228** | **228/228** | **456/456 internal checks; no independent blind claim** |

Local result filenames:
- `s25_02_m2_unified_cross84.json`
- `s25_02_m2_unified_cross72.json`
- `s25_02_m2_unified_plan_action_v1_result.json`

The 84-case provenance wrapper verifies branch, HEAD, a clean *tracked* working tree and its known manifest, then annotates the legacy runner's result. Its `legacy_reported_candidate_sha` is a historical identifier; `candidate_sha` / `evaluated_expected_sha` identify the current evaluated SHA.

All three reports: complete; critical checks pass where annotated; no observed shadow Host authorization; no failed case IDs. The 72-case reports explicitly declare `independence_verified=false`, `holdout_gate=NOT_INDEPENDENT`, `internal_validation_gate=PASS`, `m2_gate=HOLD`.

## Defect and architectural remediation

Earlier plan-resume-without-checkpoint cases, notably `simreview_no_plan_04` and `simreview_no_plan_06`, showed object/action instability under the separated semantic pipeline: resume vs step-revision and resume vs execute-first. Excluding Host plan-existence fields alone did not resolve this and caused regression.

The candidate consolidates plan creation / execute-first / resume-remaining / edit-step-definition / cancel into **one semantic plan-action decision**, avoiding duplicate competing plan decisions. Intent classification does not confer Host authorization. Missing saved plans remain Host-denied (`plan/resume` -> `deny / missing_saved_plan`); tool, task, Pending and plan-revision cases remain in the regression cohorts. There are no input-specific keyword overrides or changed sealed labels in this closure.

The passing read-only shadow evaluation does **not** itself demonstrate complete real-Host side-effect, concurrency, or durable checkpoint acceptance. Do not call the full S25-02 Step 5 accepted based solely on these cohorts.

## Integration and release separation

At closure verification, PR #22 (https://github.com/cxjchelsea/HealthAgent/pull/22) remained OPEN, base `main`, head `feat/s25-02-s25-01-main-integration-20261022`, HEAD `26d81f84dc42b4ada7bef4cf6a94da19bc7955fd`; this is **not** the tested candidate SHA. This document does not integrate this candidate into PR #22, approve merging any PR, or alter `main`.

Remaining gates:
1. Independent third-party authored and preregistered sealed review, with verified authorship / attestation and untouched cases/labels.
2. Explicit integration review of the M2 candidate into S25-02's actual PR/main-based runtime (including conflicts and real-Host Step 5 acceptance), followed by exact integrated HEAD CI and relevant manual E2E.
3. Explicit user authorization before merging; M3 cutover remains blocked until its separate conditions are met.

**Closure scope: internal M2 classifier regression only — PASS. Formal independent gate — HOLD.**
