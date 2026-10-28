# S25-02 M3-C — design and contract review (2026-10-28)

Status: DESIGN PROPOSAL / NOT IMPLEMENTED. This is an internal S25-02 milestone, not a nineteenth S2.5 capability. No merge authorization.

## Evidence baseline

- M3-B HEAD before this design: `379b40ece399b54e290f4d5af3589067ae551184`; CI run 40636145167 succeeded.
- M3-B live Qwen3 acceptance and limits: `docs/s25-02-m3-b-acceptance-2026-10-28.md`.
- Prior control-chain findings CC-02/03/06/07: `docs/s25-02-step5-control-chain-audit-20261023.md`.
- Formal S2.5 V2.0 strategy: `docs/S2.5-通用自主Agent能力补齐开发策略.md`.

## Verified gap

`apps/api/main.py` still invokes separate saved-plan intent, Understanding, clarification/capability, execution policy, plan admission, scope, and revision reviews along different paths. Some checks are essential authority checks; others duplicate semantic interpretation. `agent/route_admission_m3.py` explicitly says no API wiring and yields only `candidate_execute`, not authority. `agent/m3_turn_capture.py` and `services/plan_store.py` already provide snapshot/claim/CAS infrastructure. Avoid replacing these.

## M3-C target contract

**One primary semantic route proposal per turn, with independently verified Host admission**. Proposed routes: `respond | clarify | agent_loop | plan_create | plan_execute_first | plan_resume | plan_revise | cancel`. Proposal includes ambiguity, target, explicit current-turn user authorization evidence and bounded references to user text/verified checkpoint, never executable tool arguments sourced from model-only assumptions.

Host owns, in order:
1. Turn identity, idempotency, active-Turn exclusion and immutable preturn capture.
2. Validate proposal against user provenance, checkpoint version/digest, pending clarification origin, registered tool/schema, effect consent and plan-step state.
3. Produce typed `deny | clarify | noop | admitted` decision with reason; only `admitted` may reach an existing executor after its own final guard.
4. Persist via existing Turn transaction, PlanStore CAS, Execution Claim, and existing side-effect restrictions; no model result creates permission.
5. Return one traceable outcome, including conflicting semantic proposals and why the Host narrowed/rejected them.

The current `AdmissionDecision(candidate_execute)` is a nonauthoritative prototype. Either adapt it with explicit API facts and a separately checked final invoker gate, or introduce a minimal compatible adapter. Do not introduce another Runtime, service, database schema, or keyword router.

## Incremental implementation plan (subject to user design approval)

A. Map every early return in the production endpoint and classify **semantic proposal** vs **deterministic safety gate**. Identify which model calls can actually be eliminated without losing safety checks; count calls and keep a baseline.
B. Add a typed proposal-to-Host adapter and API contract tests in **shadow/no-effect mode first**. Keep the existing path as authoritative; record disagreements, no double execution.
C. Switch only proven-equivalent read-only and saved-plan paths behind a reversible configuration gate. Preserve all Turn/PlanStore guards. Test mixed and malformed proposals, checkpoint mismatch, missing plan, duplicate request, concurrent requests, write denial, pending clarification and cancel.
D. Run existing cross-route regression matrix (D03/D05/D06/E03/E04/E06/F02/F03/F04), CI, and repeated live Qwen3 variants. If no measurable reduction in contradictory decisions/calls or regression emerges, revert admission switch and retain evidence.

## Explicit non-goals

- S25-03 TaskState/Observation/Artifact data-model expansion.
- S25-04 tool-selection policy/registry redesign.
- S25-06 complex dependency planning, S25-07 advanced replanning, S25-11 crash recovery.
- New distributed scheduler, exactly-once claims for arbitrary external effects, or broad database migrations.
- Marking a single live Qwen success as comprehensive semantic reliability.

## Acceptance criteria

1. Standalone calculate/MCP/task/draft never implicitly resumes or creates saved plan.
2. Plan create/first/resume/revise retains checkpoint revision, verified bindings, CAS/claim, and authority; first-step and all-step scope never silently expands.
3. Pending clarification is bound to original request; unsupported capabilities fail closed without falsely denying registered tools.
4. No model/schema failure grants write authority, no repeat tool execution under same active Turn, and no false completion.
5. Fewer duplicate semantic calls on comparable traces **without** removing deterministic Host validation.
6. CI green at exact HEAD and live Qwen3 multi-phrase API evidence; remaining discrepancies documented.
7. Integration with separate PR #22/main is reviewed explicitly; no merge without authorization.

## Decision requested

Approve this bounded design before production rewiring. M4 should be defined only after M3-C integration and acceptance; do not make M3-C an open-ended refactor.
