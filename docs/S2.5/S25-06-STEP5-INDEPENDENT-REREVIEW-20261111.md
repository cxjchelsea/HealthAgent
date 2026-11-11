# S25-06 Step 5 — Independent Re-review (2026-11-11)

**Scope:** `audit/s25-06-step5-final-physical-20261111`, PR #44, based on `main@65e164e9e51ae9541507cbec36dfd8bf5add7112`. The audit evaluates actual guarantees rather than equating green CI with operational readiness. No production code change is made by this review.

## Findings and disposition

### R1 — Major evidence-scope gap (test design, now challenged)

`tests/test_s25_06_step5_postgres_late_receipt.py` uses start/go files to impose **two sequential transaction orders**. Although the writer is a separate process, the test cannot prove actual simultaneous PostgreSQL `SELECT ... FOR UPDATE` contention. The branch initially lacked direct proof of the safety of two blocked database transactions.

**Action taken:** `test_postgres_real_dispatch_row_lock_race_receipt_vs_recovery` in `tests/test_s25_06_step5_postgres_recovery.py` holds a real PostgreSQL Dispatch row lock, starts independent database connections attempting Receipt and recovery while it is held, and asserts neither finishes before release. It then accepts ONLY either (i) Receipt commits first and the pre-reviewed recovery is rejected for changed evidence; or (ii) recovery commits first and the stale Receipt is fenced. Plan remains unchanged; no duplicate Dispatch or checkpoint is authorized. **This is a real two-connection lock-contention test, not a full multi-worker service restart test.** Its result must be verified by exact-head CI.

### R2 — Production recovery trust boundary remains unintegrated (deployment blocker, not a new code bug)

`services/dependency_recovery.py` requires externally supplied `operator_access_verifier` and `worker_termination_verifier` plus explicit flags. Tests pass lambdas by design; no durable production operator IAM/policy integration or independently attested worker-termination implementation is established by this branch. In particular, the `late_receipt` recovery-first test deliberately presents a stale-token holder that remains alive while the termination verifier stub returns True. That is a **hostile fencing probe**, NOT evidence of a legitimately authorized recovery while the original worker is active.

**Disposition:** Never configure permissive production verifier stubs. Keep recovery internal and the S25-06 feature default OFF until trustworthy operator identity, access policy, worker-generation termination attestation, and a runbook are available. This can be explicitly deferred for a development-only project scope, but must not be described as completed production recovery.

### R3 — Failure-boundary coverage is now meaningful but bounded

`tests/test_s25_06_step5_postgres_process_crash.py` crashes a real worker process immediately after a PostgreSQL pre-dispatch commit, in two modes: before any invocation and **after a real trusted local MCP read returns but before persisting the Receipt**. Reconciliation must preserve `dispatch_unknown`, retain the plan and prohibit any inferred success or replay. The controlled local read does not establish remote side-effect exactly-once semantics. The test creates saved plan and Host admission evidence directly; it is **not** a complete HTTP service restart acceptance.

### R4 — Lock-order code inspection (no confirmed defect in audited paths)

- `checkpoint_dependency_step_in_session`: Plan → Claim → Dispatch → Fence.
- `_inspect_locked` + `reconcile_dependency_orphan`: Plan → Claim → Dispatch/Attempt → Fence (same transaction).
- `PlanDispatchJournal.record_receipt`: Dispatch → Fence.

For the reviewed Receipt-vs-Recovery pair, these orders do not imply a lock cycle by themselves: the Receipt path needs no Plan/Claim lock, while recovery may wait for Dispatch before reaching Fence. Reconciliation checks the immutable evidence digest again under acquired locks and revokes the old Fence before releasing Claim. However, static ordering is **not** a proof covering every other legacy M4 writer or a complete deadlock stress test.

## Required gates

1. Latest PR #44 **exact-head** CI, including all three S25-06 Step 5 dedicated PostgreSQL jobs and new two-connection row-lock race, must pass.
2. Keep real-Qwen E3 6/6 tied to its bounded trusted-local-reference case; do not generalize to arbitrary input DAGs or providers.
3. Treat production operator authority/worker-death attestation and complete FastAPI service-crash/restart as **explicitly outstanding**, or document a developer-only scope decision.
4. Do not merge PR #44 or mark the whole S25-06 completed without explicit user authorization and final accepted scope.

**Interim decision:** Conditional for test/evidence code only; no confirmed production P0/P1 flaw from these reviewed paths; full Step 5/production closeout **NOT YET**.
