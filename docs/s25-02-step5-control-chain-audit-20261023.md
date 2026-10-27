# S25-02 Step 5 — Control-chain audit and convergence (2026-10-23)

Status: IN PROGRESS. Scope: PR #22 only; no Step 6 or merge authorization.

## Baseline and observed incidents

Baseline at audit start: `8e7099bad5bc68b0d8a0791cf42f91f29e2c67fd`.
Real Qwen acceptance incidents: D03 plan authoring/execution, D05 revision/execution,
D06 resume scope, E06 drafts/write vs saved plans, F02 standalone math vs
resume, F03 missing reference term vs plan creation, F04 unsupported capability
and spurious goal clarification. Automated green does not prove live acceptance.

## Actual control flow

`apps/api/main.py`: owner/idempotency/concurrent-turn guard → load checkpoints
→ early plan step conflict check → plan intent review (if saved plan)
→ saved-plan revision review and unresolved-edit review → S25-01 Understanding
→ pending-reply classification, unsupported capability and clarification review
→ execution-policy proposal and one of its correction reviews → goal-state
admission → plan-intent handling, creation review, missing-plan review, scope
review and possible execution authorization review → AgentLoop → finalize
Turn, GoalStore, PlanStore. The model's answers are proposals, not authority.

`agent/loop.py`: read-only/write tool exposure governed by execution mode,
tool schema/allowlist, source-provenance guard for create_task/MCP, real
Observation, retry budget, PlanLedger evidence and terminal statuses.

## Findings and disposition

| ID | Finding | Risk | Disposition |
|---|---|---|---|
| CC-01 | A saved-plan turn could call `review_plan_intent` twice: once before revision and again after Understanding if the first intent was `other` or `plan_only` | Two contradictory model outputs for one request; unnecessary model latency | FIXED in this PR: reuse single turn-scoped proposal, still check step binding/authority separately |
| CC-02 | Understanding clarification can be contradicted by independent read-only completeness classification | Semantics duplicated; potential false positive/false negative | PARTIAL: typed `ClarificationRoute` now centralizes Host precedence and fail-closed decision; model-call consolidation is still OPEN |
| CC-03 | Normal requests can traverse independent execution-policy, plan-intent, plan-creation and missing-plan classifiers | Routing instability and cost | OPEN: single typed route proposal, followed by strict state and permission admission |
| CC-04 | Capability-review previously delegated tool availability to the model's `matches_available_capability` boolean | False `unsupported_capability` | IMPLEMENTED: semantic `resolved_tool_id` proposal checked against runtime registered names; unknown identifiers cannot register tools; needs live Qwen cross-language verification |
| CC-05 | Pending clarification has separate classifier; prior `needs_input:<tool>` recorded in Turn | Replay/ambiguous reply can cause wrong operation | KEEP the persisted pending-action provenance; consolidate semantic understanding without removing source/owner checks |
| CC-06 | Revision and plan scope checks are partly repeated and include contextual/error strings hardcoding specific RAG/8*8 steps | Generality and regression risk | OPEN: derive conflict descriptions from trusted current PlanLedger, not hardcoded sample steps |
| CC-07 | Read-only execution override previously set `parsed_result=None` while retaining interpretation | Implicit coupling, difficult to reason about GoalStore | PARTIAL FIX: retain Understanding result and use explicit `suppress_persistent_routing` guard for goal/plan admission and goal-store write; full typed routing remains OPEN |
| CC-08 | PlanLedger checkpoint, allowed tool set, external side effects, Turn idempotency, conversation lock and trace | Critical trust boundaries | PRESERVE; no weakening for classifier convergence |

## Target control contract (design direction, not yet implemented)

Exactly one primary *semantic* route proposal for the current turn:
`respond | clarify | agent_loop | plan_create | plan_execute_first |
plan_resume | plan_revise | cancel`, with explicit `confidence/ambiguity`
information, current-user authorization evidence, and optional bounded
argument references. Neither this result nor Understanding alone executes
tools or persists PlanLedger. Host deterministically validates checkpoint
existence/version/step binding, original-request parameter provenance,
tool schema, read/write permissions, and replay/idempotency. Only then execute
normal AgentLoop or bounded saved-plan executor. A secondary review may narrow
permissions or require clarification; it must not silently broaden write access.

Do **not** replace the running system wholesale until the contract is covered by
cross-route API tests and real model evaluations. Do **not** infer that all
findings are repaired based on this audit document.

## Required acceptance gates before Step 5 can close

1. No double plan-intent classification on saved-plan normal turns.
2. Standalone calculate/MCP/task/draft should not create or resume plans.
3. Real plan create/first/all/revise/recover preserve ledger and authority.
4. Missing arguments clarify and safely resume only with trusted pending action.
5. Unsupported tools are denied, available tools not falsely denied.
6. Model/schema failure cannot create write authority or claim success.
7. Run all existing D03/D05/D06/E03/E04/E06/F02/F03/F04 API tests, complete CI,
   then a live Qwen multi-phrase matrix. Existing manual PASS is historical
   evidence, not a blanket regression waiver.

## Convergence implementation update

At HEAD `54c580fef24e733086a757f87dfc9f11cb72f144`: F04 clarification override no longer discards parsed understanding data to indirectly skip persistence. The API keeps the original interpretation for AgentLoop and applies explicit `suppress_persistent_routing` at plan admission, goal-state admission and GoalStore save. Regression `tests/test_s25_02_control_convergence_api.py` verifies a real `calculate` division-by-zero Observation with no plan creation under a spurious Understanding clarification. This is an incremental invariant repair; CC-02/03/04 and unified routing are not yet closed.

## 2026-10-23 clarification admission convergence

Added `agent/control_admission.py` with immutable `ClarificationRoute` and `admit_clarification`. The API now translates independent reviewer outputs into one Host route: `unsupported`, `clarify`, or `read_only`. Pending tool-argument clarification cannot be treated as a fresh read-only request; only the `read_only` route suppresses persistent goal/plan operations. Added `tests/test_s25_02_control_admission.py` to cover competing inputs, write-related pending clarifications, and route invariants. This is **not** a full CC-02/CC-03 closure: primary model calls are not yet consolidated, and plan-control routing remains distributed. Do not mark Step 5 complete without real Qwen cross-route testing.

## CC-03 plan admission convergence, 2026-10-23

At commit `d74f49913242b94dc936451fc080c0146ec5d7e6`, added immutable `PlanAdmission` and `resolve_plan_admission` to `agent/plan_control.py`, integrated into the formal API, and added the cross-product state/intent admission matrix in `tests/test_s25_02_plan_route_contract.py`. Ordinary requests, new formal plans, existing-plan creation conflicts, missing checkpoints, and saved-plan execution now share one Host route outcome. No independent model classification was eliminated by *this* change; saved plan revision, execution scope and authorization remain separate pending contract-safe consolidation. This work is a **partial** CC-03 implementation, not the complete removal of duplicate semantic reviewers. Real Qwen regression is still required.

## CC-03 duplicate semantic-review reduction — 2026-10-23

Two actual calls were removed/avoided on normal structured routes:

1. A saved-plan `execute_first` proposal now goes directly to bounded first-step execution after Host checks. The independent execution-scope review is still used for `resume` to prevent accidental authorization expansion, and an unclear scope degrades to one step. A regression makes the scope reviewer raise if erroneously called for `execute_first`.
2. The existing `RevisionProposal` includes `edit_requested` as part of the *same* model output used for revision bindings. An explicit unsuccessful edit proposal reports a safe plan conflict without invoking `check_plan_edit_intent` again. The compatibility fallback remains only when legacy test/provider proposals omit the field. The Host still refuses any mutation without matching current PlanLedger binding, source-grounded old/new values, and valid step state.

These changes reduce real semantic reviews without making a model proposal an authority. Independent plan-execution authorization remains separate when an earlier policy classified a user instruction as text-only. Do not delete that safety check until the policy boundary can be proven equivalent. Live Qwen multi-phrase acceptance remains outstanding.

## CC-04 registry-grounded capability review (2026-10-23)

Removed the model-owned `matches_available_capability` field from `CapabilityReview`. The reviewer now proposes only a user-grounded literal tool reference and an optional canonical `resolved_tool_id`; `host_unsupported_capability` checks the proposed ID against the registered tool name set supplied by the API. A matching registered capability cannot be falsely marked unavailable merely because the user described it in Chinese. An absent `shell` tool is still denied when explicitly named and source-grounded; hallucinated names not mentioned by the user cannot block the request. This review does not grant execution, write authority, or construct tools. Added host boundary regression tests in `tests/test_s25_02_unsupported_capability_api.py`. Remaining: live Qwen cross-language mapping tests and checking more general unsupported feature requests; no claim of complete semantic mapping reliability.

## Execution-policy boundary convergence (2026-10-23)

Production API now calls `reconcile_execution_policy` for `respond_only` or `execute` proposals. A single structured `PolicyBoundaryReview` describes both explicit real read-only tool use and explicit mutation request, replacing two different production correction contracts. The reviewer may turn an erroneous `respond_only` classification into **read_only**, or downscope unsupported `execute` into **read_only**. It cannot promote `respond_only` to a write mode; `read_only` proposals incur no added review. Legacy `verify_text_only_policy` and `verify_execute_policy` accessors remain for backwards compatibility and should not be mistaken for production routing. Added `tests/test_s25_02_policy_reconciliation.py` to exercise seven authority combinations and malformed-schema errors. This convergence standardizes semantics but does NOT yet remove the primary separate Understanding and Execution Policy calls; claims of complete one-pass interpretation would be inaccurate. All write actions remain subject to Host consent and tool schema. Real Qwen multi-phrase testing remains outstanding.

## 2026-10-23 security review follow-up: no keyword plan execution

- **Execution authority:** `AgentLoop.run()` no longer infers authorization to execute saved pending steps from the Chinese `继续/剩余` keyword regex when `understanding_result=None`. Only an explicit Host-supplied `plan_control` (`execute_first` or `resume`) triggers the bounded saved-plan fast path; the read-only tool and argument binding checks remain. Direct-mode compatibility tests now require explicit plan-control authority for genuine continuation; a keyword-only request remains an ordinary model turn, and a negative regression checks the absence of any plan-execution/tool trace.
- **Capability ambiguity:** explicitly user-named ASCII tool identifiers missing from the registry cannot be masked by a model-suggested different registered `resolved_tool_id` (e.g. shell mapped to calculate). This is not proof that every multilingual mapping is reliable; missing tool names and paraphrases need live model review.
- **Cross-path coverage:** `tests/test_s25_02_security_crossroute_review.py` checks unsupported shell mis-mapping, available Chinese calculation naming, and no keyword-only plan execution; legacy deterministic continuation and failure-to-retry tests explicitly supply host plan control. These tests complement, not replace, D03-F04 live Qwen coverage.

**Review warning:** Removing the old shortcut intentionally changes legacy direct-mode behavior. Direct API callers relying on implicit continuation must now explicitly obtain Host plan-control authority. Do not mark the branch as release-ready without the full CI and external multi-phrase checks.
