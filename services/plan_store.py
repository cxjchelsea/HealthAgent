"""Durable per-conversation plan snapshot, owner checked by caller."""
import json

from sqlalchemy import select
from services.db import ConversationPlan


class ConversationPlanStore:
    def __init__(self, session_factory, conversation_id: str):
        self.session_factory = session_factory
        self.conversation_id = conversation_id

    def load(self):
        with self.session_factory() as db:
            record = db.scalar(select(ConversationPlan).where(
                ConversationPlan.conversation_id == self.conversation_id))
            return json.loads(record.snapshot) if record else None

    def save_in_session(self, db, snapshot: dict):
        record = db.get(ConversationPlan, self.conversation_id)
        if record is None:
            record = ConversationPlan(conversation_id=self.conversation_id)
            db.add(record)
        record.snapshot = json.dumps(snapshot, ensure_ascii=False)

    def save(self, snapshot: dict):
        with self.session_factory.begin() as db:
            self.save_in_session(db, snapshot)
