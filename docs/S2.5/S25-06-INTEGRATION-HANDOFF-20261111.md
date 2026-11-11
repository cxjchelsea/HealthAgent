# S25-06 integration handoff — rules for S2.5 peers and S3

**Status:** contract handoff, not proof that other modules have integrated with S25-06.  
Depends on the Step 6 closeout manifest and `main@ad093842a2e7631d3694c7e41a578314a36b3e26` before final closeout merge.

## Stable interfaces to preserve

| Consumer boundary | Required behavior / invariant | Evidence owner |
| --- | --- | --- |
| Semantic router / Primary | The current user request proposes a route; only `plan_create` with correctly validated `dependency_plan` may request new-format creation | `agent/primary_route_v1.py`; `tests/test_s25_06_e3_primary_format_retry.py` |
| Plan schema | `dependency-plan-v1`, `dependency-plan-read-v1`, strict DAG, pending initial statuses, pinned tool versions | `agent/dependency_plan_schema.py` |
| Saved-plan Host | Independent authorization and ownership, active Turn + Plan/Claim binding; no implicit transfer from old-format plans | `agent/dependency_plan_host.py` and Primary Host admission |
| Observation binding | A dependent input may reference only verified/checkpointed `mcp_reference_lookup.definition`, not a model-provided or uncheckpointed string | `agent/dependency_observation.py`, `agent/dependency_plan_host.py` |
| Execution | One durable pre-dispatch record before invocation, bounded Receipt, verified Attempt, monotonic per-step checkpoint | `services/dependency_dispatch.py`, `services/dependency_execution.py`, `agent/dependency_plan_runner.py` |
| Recovery | Missing Receipt stays unknown; a worker generation may be revoked only after externally authenticated authorized recovery; the old token never becomes valid again | `services/dependency_recovery.py`, `services/dependency_fencing.py` |
| Metrics / privacy | Only bounded diagnostics and durable evidence counts are exported; never export raw provider messages, tool arguments, bearer tokens or MCP source text through acceptance harness | `evals/s25_06_e3_live.py` |

**Important:** source paths are references, not invitations to bypass Host by calling internal functions as direct tools. The source observation resolver must remain read-only and version-bound.

## Explicitly out of scope for other workstreams to assume

- **Not a general workflow framework:** two registered read-only tools, no writes/medical actions, no open-ended dynamic tool catalog and no unconstrained arbitrary dependency graph guarantees.
- **Not a production recovery operator:** there is no integrated production operator IAM or trusted worker termination attestation. Do not register `reconcile_dependency_orphan` as an HTTP route, MCP capability, or model-callable tool.
- **Not an activation request:** leave `s25_06_dependency_plan_enabled` OFF by default. Any default or rollout change needs its own review.
- **Not a guarantee of S3 correctness:** medical RAG, clinical evidence, source citation quality and health decision policies require separate clinical/domain tests.

## Integration acceptance whenever a later workstream consumes S25-06

1. Use a single Host-owned admission decision; do not add a parallel keyword router or bypass the route and pending/withdrawal controls.
2. Trace source data from a trusted, versioned tool output to durable Receipt → checkpointed Attempt → `observation_ref` → target actual arguments.
3. Test interruption at every boundary: before dispatch, after tool return but before Receipt, after Receipt before checkpoint, and after checkpoint before Turn completion. Unknown outcomes must never be automatically re-executed.
4. Verify idempotence and CAS/Claim/Fence under the intended actual database and concurrency topology; SQLite tests alone do not certify PostgreSQL operations.
5. If adding tools/Observation fields, explicitly modify the generation schema, strict host validator, allowlist and privacy-safe evidence projection together; prohibit silent coercion or automatic argument repair that could mint authority.
6. Do not claim production readiness without independently trusted access and worker-death verification plus service-level restart acceptance.
7. Keep development tests with synthetic or approved non-sensitive data. Do not use real medical records to test this infrastructure feature.

## Practical local verification (development only)

With the repository's existing virtualenv and isolated test services configured:

```powershell
cd D:\HealthAgent
git switch main
git pull --ff-only origin main
python -m pytest -q
```

For the PostgreSQL physical suite, configure `TEST_POSTGRES_URL` to a **dedicated disposable PostgreSQL test database**; those tests create/delete rows and MUST NOT run against production data. The three Step 5 files are:

```powershell
python -m pytest -q -rs tests/test_s25_06_step5_postgres_recovery.py tests/test_s25_06_step5_postgres_process_crash.py tests/test_s25_06_step5_postgres_late_receipt.py
```

For live Qwen E3 acceptance, use the **current** `main` or the reviewed Step 6 branch, follow `docs/S2.5/S25-06-SLICE-E3-LIVE-ACCEPTANCE-20261111.md` for provider/identity isolation, and run `python -m evals.s25_06_e3_live --repeats 2 --output artifacts/s25-06-e3-live-recheck.json` only with the explicit opt-in environment; this is optional re-validation, not an invitation to commit credentials or raw reports.

**Handoff disposition:** development-scope contract ready for other workstreams to reference; consumer integration and production deployment are separate future acceptance decisions.
