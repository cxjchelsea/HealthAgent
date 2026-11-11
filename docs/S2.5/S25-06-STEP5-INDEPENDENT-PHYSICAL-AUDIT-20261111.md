# S25-06 Step 5 — Independent audit and physical acceptance ledger

**Immutable baseline:** `main@65e164e9e51ae9541507cbec36dfd8bf5add7112` (merged PR #43). Review the implementation separately from its positive test assertions. This ledger is a tracked acceptance contract, **not** a claim of production deployment or of final Step 5 closure.

## Evidence already present at baseline

| Boundary | Grounded implementation / check | What it actually proves |
| --- | --- | --- |
| Primary → Host creation | E1/E2 tests, `agent/primary_route_v1.py`, `agent/primary_host_admission_v1.py` | Typed dependent plan is not itself permission; Host checks before atomic creation |
| Real model | Operator-provided E3 v7 JSON, 2 independent conversations / 6 stages PASS | Actual `ollama_proxy/qwen3:14b` CREATE → FIRST → RESUME on transient SQLite + trusted local reference catalog |
| Tool evidence | `services/dependency_dispatch.py`, `services/dependency_execution.py`, `tests/test_s25_06_slice_d_http.py` | Dispatch-before-tool, Receipt, Attempt and checkpoint; no automatic retry of unknown dispatch |
| PostgreSQL committed-step atomicity | `tests/test_s25_06_slice_d_postgres.py` | Real PostgreSQL transactions for one checkpoint and two racing finalizers; **not** a process-kill proof |
| PostgreSQL double creation | `tests/test_s25_06_slice_e2_postgres_two_creators.py` | Two processes: incumbent running Turn wins, second receives 409 before Primary; **not** concurrent finalization of two admitted creators |
| Orphan recovery semantics | `services/dependency_recovery.py`, `services/dependency_fencing.py`, `tests/test_s25_06_slice_d_recovery.py` | Explicit external authorization, independent worker termination verifier, immutable evidence digest, monotonic generation fence; recovery test previously uses SQLite and test verifier stubs |
| Main CI baseline | PR #43 run `42943343389` | `1494 passed, 5 skipped` and existing targeted PostgreSQL jobs passed, including E2 dual HTTP worker |

## Newly added in this audit branch

- `tests/test_s25_06_step5_postgres_recovery.py`: *real PostgreSQL* orphan `dispatch_unknown` state, negative termination proof, successful synthetic-authority reconciliation, atomic claim release + fence revocation, original checkpoint and dispatch preservation, old fence cannot commit, repeated inspection denied. An added **two-connection row-lock contention test** holds the physical Dispatch lock, starts a Receipt writer and recovery concurrently, then accepts only the two safe serializations (Receipt first → stale evidence rejection; recovery first → late Receipt fenced). This is not a two-process simultaneous HTTP race.
- `tests/test_s25_06_step5_postgres_process_crash.py`: separate worker process writes its actual new-format fenced pre-tool dispatch to PostgreSQL, then terminates abruptly via `os._exit(73)`. Parent process verifies durable `dispatch_unknown`, performs evidence-bound recovery with termination verified by observed subprocess exit, and checks no Receipt/Attempt/replay and permanent fencing.
- CI runs **both** tests explicitly with `TEST_POSTGRES_URL`. Operator authority is represented by independently checked test-only verifiers; **not** production IAM. The abrupt-exit test kills the worker **before tool invocation** and does not prove post-call external effect uncertainty or recovery versus a still-active worker.

- `tests/test_s25_06_step5_postgres_late_receipt.py`: independently launched process covers both deterministic commit orders: (A) recover and revoke before a stale-token Receipt attempt, which must be rejected, and (B) commit a test-only observed Receipt before inspection/recovery, which must be preserved without automatic checkpoint. No real external tool result is claimed. **This does not force simultaneous row-lock contention.**
- The abrupt process-crash test now has **two** PostgreSQL modes: no tool call and a real trusted local read-tool result followed by `os._exit(73)` **before** Receipt persistence. Both must retain unresolved `dispatch_unknown` and never automatically replay. No external side-effect semantics or exactly-once are inferred.

## Final outstanding gates (not auto-waived by passing CI)

1. **Full HTTP process termination / restart** on isolated PostgreSQL: subprocess tests now cover pre-call and post-local-MCP-result/pre-Receipt exits, but not a full FastAPI worker restart, remote MCP with effects, or exactly-once external delivery.
2. **Operator access + worker termination verification:** independently configured trusted verifiers (not caller-supplied boolean attestations or testing lambdas). There is no production-grade verifier implementation in the present audited service boundary.
3. **Concurrency across recovery and late worker receipt:** two-process deterministic commit orders are covered, and a separate two-connection PostgreSQL row-lock contention test now probes both possible serializations. Production-scale multi-process scheduling and operator/worker-termination attestation remain outside this proof.
4. **E3 beyond happy path:** independent provider/model evaluation of invalid graph, spoofed source, illegal write-tool request, ambiguous provider completion; real Qwen 6/6 covers only one trusted two-step positive task.
5. **Final evidence release review:** ensure no Prompt/Receipt/arguments/credential data exported in accepted reports; do not claim external exactly-once effects.
6. **Default-OFF and regression:** full CI, legacy paths, and real PostgreSQL gates at the **final** audit commit, plus explicit user authorization before merging this branch.

## Review disposition

- E3 acceptance = **PASS** for the bounded real-provider positive case.
- Existing safety architecture = **designed to fail closed** with test-backed scope specified above.
- Step 5 complete = **NOT YET** until unresolved physical-process and operator verification gates are addressed or explicitly accepted as documented non-production limits.
- Do not alter the user-facing feature flag default or introduce a public recovery endpoint as part of audit.
