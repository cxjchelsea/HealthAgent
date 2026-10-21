"""S25-01 minimal durable goal context. Only trusted host applies goal changes."""
import json
from sqlalchemy import select
from agent.understanding import GoalSpec, UnderstandingResult, UnderstandingError
from services.db import ConversationGoalState

class ConversationGoalStore:
    def __init__(self, session_factory, conversation_id):
        self.Session, self.conversation_id = session_factory, conversation_id

    def load(self):
        with self.Session() as db:
            row = db.get(ConversationGoalState, self.conversation_id)
            if not row:
                return {"version": 0, "goal": None, "pending": None}
            return {"version": row.version,
                    "goal": json.loads(row.goal_json) if row.goal_json else None,
                    "pending": json.loads(row.pending_json) if row.pending_json else None}

    def validate_before_execution(self, result: UnderstandingResult, previous: dict):
        """Reject unsafe goal transitions before any Agent tool could run."""
        kind = result.change.kind
        if result.status != "understood":
            return
        if kind in {"add", "revise", "clarify_response"}:
            base = previous.get("goal")
            if base is None and kind == "clarify_response":
                base = (previous.get("pending") or {}).get("goal")
            if base is None or result.goal is None:
                raise UnderstandingError("missing_confirmed_goal")
            old = GoalSpec.model_validate(base)
            proposed = result.goal
            prior = {(entry.name, entry.scope): entry.value for entry in old.constraints
                     if entry.source in {"explicit", "confirmed"}}
            updated = {(entry.name, entry.scope): entry.value for entry in proposed.constraints}
            if any(key not in updated or updated[key] != value for key, value in prior.items()):
                raise UnderstandingError("goal_change_needs_confirmation")

    def save_in_session(self, db, result: UnderstandingResult, previous: dict):
        """Commit only validated proposals, CAS against the prior loaded revision.

        A model-generated goal is a user-intent proposal, not an authority to
        mutate the PlanLedger, write external data, or grant tool approval.
        """
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
            if kind == "new_goal":
                goal = result.goal.model_dump(mode="json") if result.goal else None
                pending = None
            elif kind in {"add", "revise", "clarify_response"}:
                if goal is None and kind == "clarify_response":
                    goal = (pending or {}).get("goal")
                if goal is None or result.goal is None:
                    raise UnderstandingError("missing_confirmed_goal")
                # The model proposes the complete updated target; host verifies
                # existing explicit constraints cannot silently disappear.
                old = GoalSpec.model_validate(goal)
                new = result.goal
                prior = {(c.name, c.scope): c for c in old.constraints
                         if c.source in ("explicit", "confirmed")}
                fresh = {(c.name, c.scope): c for c in new.constraints}
                removed = set(prior) - set(fresh)
                if removed:
                    raise UnderstandingError("constraint_removal_requires_confirmation")
                if any(prior[k].value != fresh[k].value for k in prior if k in fresh):
                    # Explicitly changed constraints need next-turn confirmation;
                    # don't silently overwrite committed facts.
                    pending = result.model_dump(mode="json")
                else:
                    goal = new.model_dump(mode="json")
                    pending = None
            elif kind in {"continue", "chat", "query_status", "cancel_request"}:
                # "Cancel" is an intent, not permission to cancel a running task.
                pending = None if kind != "cancel_request" else result.model_dump(mode="json")
        row.goal_json = json.dumps(goal, ensure_ascii=False) if goal else None
        row.pending_json = json.dumps(pending, ensure_ascii=False) if pending else None
        row.version = version + 1
