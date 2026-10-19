"""Durable task state scoped to a single conversation."""
import uuid
from sqlalchemy import select, func
from services.db import ConversationTask

class ConversationTaskStore:
    def __init__(self, Session, conversation_id):
        self.Session = Session
        self.conversation_id = conversation_id

    def create(self, title):
        with self.Session.begin() as db:
            count = db.scalar(select(func.count()).select_from(ConversationTask).where(
                ConversationTask.conversation_id == self.conversation_id))
            if count >= 20:
                return {"ok": False, "error": "task_limit"}
            task = ConversationTask(id=str(uuid.uuid4()), conversation_id=self.conversation_id, title=title)
            db.add(task)
            db.flush()
            return {"ok": True, "task_id": task.id, "title": task.title}

    def list(self):
        with self.Session() as db:
            records = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == self.conversation_id).order_by(
                ConversationTask.created_at, ConversationTask.id)).all()
            return {"ok": True, "tasks": [{"task_id": t.id, "title": t.title} for t in records]}
