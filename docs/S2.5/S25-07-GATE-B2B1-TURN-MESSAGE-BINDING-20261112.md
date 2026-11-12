# S25-07 Gate B2b1 — exact Turn/user-message linkage (2026-11-12)

**Base:** `main@5e93d4dbc9c4de1106e8c76466d5fb6a5f383440` (B2a PR #52 merged).

## Implemented in this slice

- A nullable `Turn.user_message_id` FK **and unique constraint** are introduced by Alembic migration `0021_turn_user_message`. Historical turns remain NULL; no chronology-based guesswork/backfill. A concrete user Message can be bound to at most one Turn at the database level. This is a write-once API ownership convention, not a cryptographic proof that privileged DB writers cannot change rows.
- The existing `/conversations/{cid}/agent/messages` Turn creation writes the **new user Message ID** in the same initial transaction before any Primary/Host decision. The normal route behavior, model and tool capabilities remain unchanged.
- The B2a revision commit now requires `Turn.user_message_id == user_message_id` (in addition to owner, conversation, request ID, immutable PrimaryRouteEvidence and persisted message content). An older user message from the same conversation or an unbound legacy Turn cannot authorize a revision. The commit method remains unexposed to HTTP.
- Existing B2a SQLite and PostgreSQL tests bind the current message to the Turn and add specific negative cases for absent linkage and old same-conversation messages. Migration head assertion and new column assertion are updated.

## Strictly NOT delivered

- B2b2: bounded model-proposed versioned pending suffix, Primary route schema, deterministic Host revision admission and immutable revision-intent evidence.
- B2b3: safe result staging and atomic `Plan + TaskState + Turn + assistant Message` finalization via the **official HTTP path** and real PostgreSQL negative tests.
- B3: revised executor or cross-revision RESUME; Gate C: any retry after failure_finalized.

Do not enable plan revision in production yet: B2a still requires an admission reason that existing Primary Host does not emit. B2b1 closes only the exact-message association prerequisite. Changes remain opt-in in their effect because existing route denies dependency revisions.

**Status:** candidate for independent review, not Gate B2b overall completion. PR remains Draft; no merge absent explicit user authorization.
