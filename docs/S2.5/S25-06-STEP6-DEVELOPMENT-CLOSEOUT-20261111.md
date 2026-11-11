# S25-06 Step 6 — Development-scope closure and evidence manifest

**Closure candidate only; requires independent review and explicit merge authorization.**  
Baseline: `main@ad093842a2e7631d3694c7e41a578314a36b3e26` (PR #44 merged).  
Scope: HealthAgent S2.5-06 **guarded read-only dependent plan**. This is not certification for production clinical use, arbitrary DAG execution, external exactly-once semantics, or production recovery IAM.

## 1. Bounded capability and ownership

S25-06 implements an opt-in, versioned `dependency-plan-v1` with `dependency-plan-read-v1` executor. It supports **2–8** pending steps in an ascending topologically ordered dependency graph, within a **strict trusted read-only pair**:

- Source: `mcp_reference_lookup@1.0.0`; exact `term` literal grounded in the current request.
- Dependent target: `text_stats@1.0.0`; `text` is an `observation_ref` to a verified preceding `mcp_reference_lookup` receipt's `definition` field.
- CREATE saves an entirely pending plan and executes **no** tools. FIRST executes **only** the first eligible step. RESUME uses the saved checkpoint and trusted evidence to run remaining eligible steps.
- The LLM proposes a typed plan; **Primary Host admission** owns authorization, and a durable **Claim + generation Fence** protects the executor. The pre-dispatch journal, bounded Receipt, per-step Attempt and checkpoint must remain consistent. A missing Receipt means **unknown**, not success or permission to replay.
- Legacy saved plans are not silently migrated into `dependency-plan-v1`; incompatible/invalid plans fail closed. The S25-06 feature remains **default OFF** (`Settings.s25_06_dependency_plan_enabled=False`).

This description does **not** mean all possible valid DAG topologies, providers, user requests or external MCP tools have been tested or made available.

## 2. Merge provenance

| Slice / phase | PR | Merge commit | Accepted contribution |
| --- | --- | --- | --- |
| A | #36 | `fec0f2071a3d937f53f620416c5b327832cfe87f` | Strict versioned dependency creation/schema contract |
| B | #37 | `a513e73db3a7b7af2d04b421ab42506cf3a15c46` | Checkpointed observation resolver |
| C | #38 | `00eb5a35d714d0b74659db6ec6a1d793839939d7` | Bound Host read admission |
| D | #39 | `64fe592df87b994bf73da210963c2e89561f8067` | Gated HTTP execution and atomic per-step checkpoint |
| E1 | #41 | `f5291e436b144a639fce05265cfd2b3a9545e7b0` | One-Primary typed dependent-plan proposal |
| E2 | #42 | `4dcb57ceaa8f16e4a094eb351012527616dc7f71` | Host admission and atomic new-plan creation |
| E3 | #43 | `65e164e9e51ae9541507cbec36dfd8bf5add7112` | Opt-in real-Qwen HTTP acceptance harness and contract corrections |
| Step 5 | #44 | `ad093842a2e7631d3694c7e41a578314a36b3e26` | Independent audit and real PostgreSQL crash/fence/lock contention evidence |

## 3. Evidence with exact limits

| Evidence | Result | Boundaries it supports |
| --- | --- | --- |
| User-operated E3 real-model report `s25-06-e3-live-v7.json` | **6/6 PASS**, two independent CREATE → FIRST → RESUME sequences | Real `ollama_proxy/qwen3:14b`; synthetic identity, isolated SQLite, trusted local MCP and a single two-step positive scenario. The user-provided report is **not committed to the repository**. |
| PR #43 CI, run `42943343389` | **1494 passed / 5 skipped**, workflow success | Offline validation and existing PostgreSQL regression jobs at that commit |
| PR #44 final CI, run `42948214282` | **1500 passed / 5 skipped**, workflow success | Existing regressions, dedicated PostgreSQL orphan recovery, controlled crash before and after trusted local MCP return, both deterministic Receipt/recovery orders, plus genuine two-connection Dispatch row-lock contention |
| `tests/test_s25_06_step5_postgres_recovery.py` | PostgreSQL physical PASS | Evidence-bound release, operator-verifier negative gate, Claim release, permanent old-generation Fence, and two-connection lock serialization |
| `tests/test_s25_06_step5_postgres_process_crash.py` | PostgreSQL physical PASS | Abrupt subprocess exit before tool or after local read result but before Receipt; unknown outcome never inferred as success or auto-replayed |
| `tests/test_s25_06_step5_postgres_late_receipt.py` | PostgreSQL physical PASS | Deterministic old-token Receipt-before-recovery and recovery-before-Receipt orders. Test operator verifiers are **not** production IAM |

The test counts are historical CI measurements at their specified commits. CI success in Step 6 is required at the Step 6 exact HEAD too; do not copy a past result as a new run.

Source-of-truth detail: `docs/S2.5/S25-06-STEP5-INDEPENDENT-PHYSICAL-AUDIT-20261111.md`, `docs/S2.5/S25-06-STEP5-INDEPENDENT-REREVIEW-20261111.md`, `docs/S2.5/S25-06-SLICE-E3-LIVE-ACCEPTANCE-20261111.md`, plus the named tests and actual CI jobs. Historical documents may retain their original unchecked checklist; this manifest records the supported status *after* PR #44.

## 4. Residual risk register (not completed / not waived as production-ready)

| ID | Residual | Disposition for **development-only** S25-06 |
| --- | --- | --- |
| R-01 | Independently trusted production operator identity, authorization, and immutable worker termination attestation are not implemented | **Deferred / deployment blocker**; no permissive test verifiers in production |
| R-02 | Complete FastAPI multi-worker kill/restart, real production IAM and remote effectful MCP crash boundaries unverified | **Deferred / deployment blocker**; tests cover bounded local read worker processes and PostgreSQL transactions, not full service disaster recovery |
| R-03 | Real-model adversarial/unsupported DAGs and other providers not covered by E3 6/6 positive scenario | **Deferred / expansion acceptance**; keep unsupported requests fail-closed |
| R-04 | Exactly-once external side effects cannot be inferred from database Receipt/Fence | **Not promised**; no side-effectful dependency tools in this slice |
| R-05 | Full S2.5 cross-workstream and S3 medical knowledge integration has not been independently accepted by S25-06 tests | **Separate integration gate**; no S3 readiness claim |
| R-06 | E3 report intentionally excludes raw prompts, credentials, arguments, provider outputs and receipts | **Preserve privacy**; only privacy-safe evidence and explicit environment/source provenance to be retained |

These are not newly discovered P0/P1 implementation defects. They explicitly bound what Step 6 may close and what future activation must verify.

## 5. Non-regression and feature activation contract

1. No change to `Settings.s25_06_dependency_plan_enabled=False` by this closeout. A deliberate feature rollout is a new review/authorization task; an active real-provider setting additionally requires explicit non-demo identity.
2. Model-supplied `dependency_plan`, tool names, observation values, prompts, previous chat content and provider schema outputs are never authority to execute, reconcile, modify a tool registry or mint an operator permission.
3. No fallback to legacy plan execution when a versioned plan exists. Never retry `dispatch_unknown` or infer a success from a response narrative.
4. Any future extension of approved tools, argument bindings, Observation fields, providers or persistence backends needs contract/schema, Host, privacy and regression acceptance together.
5. The S25-06 developer-scope exit criterion is **completed feature + accepted bounded evidence + residual ledger + exact-HEAD CI + explicit user acceptance**; production deployment has a separate gate.

## 6. Step 6 exit checklist

- [x] Provenance traced to merged PRs #36–#44 and immutable main baseline.
- [x] Real-model **6/6** positive scenario distinguished from broader unverified claims.
- [x] PostgreSQL failure/recovery/race evidence linked with strict test limitations.
- [x] Production blockers and future integration requirements explicitly listed.
- [x] Default-OFF, Host authorization, Claim/Fence/Receipt and no-replay boundaries documented.
- [ ] Step 6 branch final CI green at exact HEAD, independent final review completed.
- [ ] PR reviewed and merged **only following explicit user authorization**; then verify main includes the archived closeout.

**Disposition before the last two checks:** S25-06 is *eligible for development-scope closure*, **not production-ready and not yet formally archived**.
