"""S25-01 minimal durable goal context. Model interpretation is a proposal, not execution authority."""
import json
from agent.understanding import GoalSpec, UnderstandingResult, UnderstandingError
from services.db import ConversationGoalState

class ConversationGoalStore:
    def __init__(self, session_factory, conversation_id):
        self.Session = session_factory
        self.conversation_id = conversation_id

    def load(self):
        with self.Session() as db:
            row = db.get(ConversationGoalState, self.conversation_id)
            if row is None:
                return {"version": 0, "goal": None, "pending": None}
            return {"version": row.version,
                    "goal": json.loads(row.goal_json) if row.goal_json else None,
                    "pending": json.loads(row.pending_json) if row.pending_json else None}

    @staticmethod
    def _constraint_change(before: GoalSpec, after: GoalSpec) -> bool:
        old = {(item.name, item.scope): item.value for item in before.constraints
               if item.source in ("explicit", "confirmed")}
        new = {(item.name, item.scope): item.value for item in after.constraints}
        return any(key not in new or new[key] != value for key, value in old.items())

    def validate_before_execution(self, result: UnderstandingResult, previous: dict) -> str:
        """Classify a goal mutation BEFORE AgentLoop can produce external effects."""
        if result.status != "understood":
            return "ok"
        kind = result.change.kind
        pending = previous.get("pending")
        if kind == "clarify_response" and pending and (
                (pending.get("change") or {}).get("kind") == "revise"):
            # Only the exact stored proposal may be confirmed; no new mutation
            # can be smuggled into the confirmation turn.
            if result.goal is None or result.goal.model_dump(mode="json") != pending.get("goal"):
                raise UnderstandingError("confirmation_proposal_mismatch")
            return "ok"
        if kind in {"add", "revise", "clarify_response"}:
            existing = previous.get("goal")
            if existing is None and kind == "clarify_response":
                existing = (pending or {}).get("goal")
            if existing is None or result.goal is None:
                raise UnderstandingError("missing_confirmed_goal")
            if self._constraint_change(GoalSpec.model_validate(existing), result.goal):
                if kind == "revise":
                    return "confirmation_required"
                raise UnderstandingError("constraint_change_requires_confirmation")
        return "ok"

    def save_in_session(self, db, result: UnderstandingResult, previous: dict):
        """Version-checked local commit; never change PlanLedger or authorize tools."""
        row = db.get(ConversationGoalState, self.conversation_id)
        version = row.version if row else 0
        if version != previous["version"]:
            raise RuntimeError("stale_goal_version")
        if row is None:
            row = ConversationGoalState(conversation_id=self.conversation_id, version=0)
            db.add(row)
        goal = previous.get("goal")
        pending = previous.get("pending")
        kind = result.change.kind
        if result.status == "clarification_required":
            pending = result.model_dump(mode="json")
        elif result.status == "understood":
            decision = self.validate_before_execution(result, previous)
            if decision == "confirmation_required":
                # Keep the already committed goal unchanged; capture the proposal.
                pending = result.model_dump(mode="json")
            elif kind == "new_goal":
                goal = result.goal.model_dump(mode="json") if result.goal else None
                pending = None
            elif kind in {"add", "revise", "clarify_response"}:
                goal = result.goal.model_dump(mode="json") if result.goal else goal
                pending = None
            elif kind == "cancel_request":
                pending = result.model_dump(mode="json")
            # Continue/chat/status retain current confirmed goal and pending state.
        row.goal_json = json.dumps(goal, ensure_ascii=False) if goal else None
        row.pending_json = json.dumps(pending, ensure_ascii=False) if pending else None
        row.version = version + 1

    def resolve_confirmation_in_session(self, db, previous: dict, expected_version: int,
                                        accept: bool) -> str:
        """Explicit host-confirmed mutation; never delegated to an LLM."""
        row = db.get(ConversationGoalState, self.conversation_id)
        if row is None or row.version != expected_version or previous["version"] != expected_version:
            raise UnderstandingError("stale_goal_confirmation")
        pending = previous.get("pending")
        if not pending or (pending.get("change") or {}).get("kind") != "revise":
            raise UnderstandingError("no_pending_revision")
        if accept:
            parsed = UnderstandingResult.model_validate(pending)
            if parsed.goal is None:
                raise UnderstandingError("invalid_pending_revision")
            row.goal_json = json.dumps(parsed.goal.model_dump(mode="json"), ensure_ascii=False)
        row.pending_json = None
        row.version += 1
        return "confirmed" if accept else "rejected"
