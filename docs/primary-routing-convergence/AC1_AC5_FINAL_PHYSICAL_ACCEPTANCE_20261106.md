# AC1–AC5 full-business physical acceptance — 2026-11-06

## Evidence-based decision

**Full-business Qwen v2 matrix: PASS (23/23, 0 FAIL, 0 BLOCKED) on exact HEAD `575e747d69abf6e5af29df803e0a2338f34665a5`.** The full test **scope** can be closed; merging the entire branch still requires a PR-level diff, branch-protection, and mergeability review and explicit user authorization.

- Original user-run artifact: `ac1-ac5-business-v3.json`, created UTC `2026-11-06T01:43:35.427305+00:00`.
- SHA-256 of original artifact bytes: `5e0c0b3d1b4c31accfb79ed2afe2945b750cd4eabc7ea16871e0959c7b44cba9`. Preserve original report outside Git; this document is a bounded evidence index, not a replacement for the original report.
- `schema_version=ac1-ac5-business-physical-v2`, `manifest_sha256=6f1be7ab00dfd8e011f612430b993cc0847111fddb47ea3ef0bda3add11f6cc3`, all scenario groups selected.
- Local model `ollama_proxy/qwen3:14b`, Unified true; all regular semantic routes show `primary=1` and `semantic_attempts=1`. `other` calls represent non-Primary model work. No retired category appears in the test's reported model categories.
- GitHub Actions `42143587262` on the **same HEAD**: SUCCESS; pytest `1184 passed, 3 skipped`; targeted PostgreSQL Pending confirmation `5 passed`. See https://github.com/cxjchelsea/HealthAgent/actions/runs/42143587262

## Positive business acceptance

| Business family | Scenarios | Outcome |
| --- | --- | --- |
| Plan A | CREATE / FIRST / RESUME | 3/3 PASS; step progression pending+pending → completed+pending → completed+completed; exactly two calculate Attempts |
| Plan B | CREATE / REVISE / CANCEL | 3/3 PASS; durable plan revision and plan cancellation |
| Ordinary | calculate / direct create_task / replay same request ID | 3/3 PASS; calculation Attempt; task and Attempt each exactly one; 409 replay without physical row changes |
| Memory | without consent / with consent | 2/2 PASS; denied attempt has no write, consented attempt saved test value |
| Pending task | origin / title stage / structured confirmation / replay | 4/4 PASS; `pending → awaiting_confirmation → consumed`, zero Task/Attempt before confirm, exactly one of each after, 409 replay without row changes |
| Pending withdrawal and topic switch | origin/reply ×2 | 4/4 PASS; both reply routes `respond`, authorities revoked, zero Task/Attempt |
| Pending MCP | origin/answer | 2/2 PASS; persisted typed `needs_input:mcp_reference_lookup`; answer `clarify_response`, Host `allow`, one successful `mcp_reference_lookup` Attempt, zero Task |
| Unknown saved plan | plan_missing | 1/1 PASS; restricted and no execution |
| Preflight | real model readiness | PASS |

## Important audit interpretation

1. `PENDING_TASK_CONFIRM.physical.turn_evidence_ok=false` is **expected**, not a failed assertion. The structured confirmation API creates a third Turn and consumes the authority; it does not run a new semantic Primary proposal. Its independent physical contract checks exact completed Turn, one task, one successful bound Attempt, consumed authority and **two** earlier Primary evidence rows; this case is scored PASS only if `effect_ok=true` and there is no model-routing call.
2. The report's `memory_rows` is a helper count for the synthetic `user_memories.key`, **not** per-conversation isolation evidence (memory is owner-scoped by design). Owner and cross-user isolation depend on other targeted tests and cannot be inferred from this matrix alone.
3. This is 23 **assertion checkpoints**, not 23 statistically independent language samples or proof of 100% semantic accuracy. It is a narrow regression suite, not production medical or external-tool validation.
4. Real-model Qwen first-run failure `PENDING_MCP_ORIGIN` was separately diagnosed as `primary_route_pending_read_invalid`; subsequent follow-up routing failure chose `clarify`. The focused MCP Prompt and typed origin-marker physical test restored both on the same final HEAD. This acceptance does not erase old failed artifacts.
5. Pre-existing PostgreSQL crash/race tests and the rest of AC2/AC4 safety contract are validated by CI in their own dedicated jobs; this Qwen matrix does **not** replace them.

## Merge gate (not yet satisfied)

- Current branch `refactor/primary-routing-convergence`; `main@41d91a3ae3fceeaa6f5401313f7471285b8cd86f`. Comparison at this audit: ahead 354 commits, behind 0, 116 files changed. This is a **large cross-component change**, not a narrowly scoped MCP patch.
- No currently open PR for this branch was found in the GitHub open-PR list as of this review.
- A PR-level diff review must check runtime and migration scope, all check requirements, code ownership, no accidental artifacts/secrets, and exact PR head SHA. This document commit changes HEAD, so rerun CI on its latest HEAD before treating it as checked.
- Do not merge without explicit user authorization, and when authorized use Standard Merge Commit only, honoring branch protection and follow-up post-merge verification.

## Next action

Create and review a PR only after the final diff and HEAD-level required checks are verified. Preserve original artifact SHA and the CI URL in the PR review summary. Avoid further prompt modifications without new failing evidence.
