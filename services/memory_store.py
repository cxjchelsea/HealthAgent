"""Explicit, owner-scoped long-term memory with deterministic exact-key access."""
import uuid
from sqlalchemy import select
from services.db import UserMemory

class MemoryStore:
    def __init__(self, Session, owner_id, *, write_allowed=False):
        self.Session = Session
        self.owner_id = owner_id
        self.write_allowed = write_allowed

    def put(self, key, value):
        if not self.write_allowed:
            return {"ok": False, "error": "memory_write_requires_user_consent"}
        with self.Session.begin() as db:
            item = db.scalar(select(UserMemory).where(
                UserMemory.owner_id == self.owner_id, UserMemory.key == key))
            if item is None:
                item = UserMemory(id=str(uuid.uuid4()), owner_id=self.owner_id,
                                  key=key, value=value)
                db.add(item)
            else:
                item.value = value
            return {"ok": True, "key": key, "saved": True}

    def get(self, key):
        with self.Session() as db:
            item = db.scalar(select(UserMemory).where(
                UserMemory.owner_id == self.owner_id, UserMemory.key == key))
            return {"ok": True, "found": item is not None,
                    "key": key, "value": item.value if item is not None else None}

    def list(self):
        with self.Session() as db:
            items = db.scalars(select(UserMemory).where(
                UserMemory.owner_id == self.owner_id).order_by(UserMemory.key)).all()
            return [{"key": item.key, "value": item.value} for item in items]

    def delete(self, key):
        with self.Session.begin() as db:
            item = db.scalar(select(UserMemory).where(
                UserMemory.owner_id == self.owner_id, UserMemory.key == key))
            if item is None:
                return False
            db.delete(item)
            return True
