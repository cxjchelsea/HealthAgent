# S25-07 Gate B2b2 — Primary semantic proposal + deterministic Host admission (2026-11-12)

**Base:** `main@b2db586d10e7060d631cd84a25138117938ba87d` after B2b1 PR #53 merge.

## Delivered

- `S25_07_DEPENDENCY_REVISION_PROPOSAL_ENABLED=false` is a new **default-OFF** configuration option. It requires the existing S25-06 dependency-plan flag and a saved dependency-format plan before the official Primary route can propose a versioned pending edit.
- The SAME `primary-route-v1` model output carries an optional `dependency_revision_steps` with the complete pending suffix; `plan_revise` retains an explicit 1-based `step_number`. No second semantic classifier or post-hoc route rewrite is introduced.
- The Primary binder compiles the proposal against the *exact pre-Turn raw snapshot* via B1's `compile_revision_candidate`. It denies unsupported parent versions, completed-step edits, tool/version/dependency substitutions, non-user-grounded new literal values and target mismatches. B2b2 conservatively permits only one explicitly targeted pending step to change; other pending steps remain identical. Changed goal text must appear in the current user message. The Host also rejects `text_stats` literal bindings, since the current dispatcher accepts only verified `observation_ref` inputs; a valid B1 proposal is not by itself an executable B3 plan.
- Deterministic Host independently revalidates the original parent JSON, quote, B1 candidate, selected target and changed goal against the current request. It emits `allow/dependency_revision_candidate` **only for a valid proposal**. The existing write-once `PrimaryRouteEvidence` captures this reason, route and parent snapshot digest before terminal response.
- The official HTTP Primary route can report `dependency_revision_admitted_not_saved` with status `partial`. This is **not** a plan checkpoint, success claim or execution right: no Plan, TaskState or Dispatch changes occur. Replayed client_request_id is still rejected by existing Turn uniqueness.

## Explicit incompleteness

**B2b3** must connect the proposed pending suffix to the B2a transaction in the same official HTTP terminal commit, ensuring Turn still running, the exact immutable user Message and Primary Evidence match, and Plan/TaskState/Turn/assistant Message commit or roll back together. The B2b2 HTTP result deliberately terminalizes the Turn without saving revision 2; it is not replayable as B2a CAS authority after the fact. Do NOT expose B2a standalone or add a second routing owner.

**B3** must implement cross-revision evidence and executable resume. **Gate C** handles revised attempt model / bounded retry. No revision 2 execution, retry, re-dispatch, Claim or Fence is authorized by B2b2.

## Review expectations

Validate default-OFF schema and routes, explicit opt-in source-grounded proposal, changed single target, forged BoundPrimaryProposal denial, write-once parent evidence, HTTP pending-not-saved semantics and zero tool invocation under SQLite / PostgreSQL. All changes require independent review and explicit merge authorization.
