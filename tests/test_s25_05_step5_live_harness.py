"""Offline contract tests for the S25-05 real-provider acceptance harness.

These tests do NOT claim to exercise a real model. They validate fail-closed
evidence scoring and prevent accidental fake-provider execution.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from agent.config import Settings
from services.db import Base, Conversation
from evals.s25_05_step5_live import inspect_response, evaluate


class Response:
    status_code = 200
    headers = {"X-HealthAgent-Model-HTTP-Attempts": "1"}

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_step5_live_requires_explicit_real_provider(monkeypatch):
    monkeypatch.delenv("RUN_S25_05_LIVE", raising=False)
    with pytest.raises(RuntimeError, match="RUN_S25_05_LIVE"):
        evaluate()
    monkeypatch.setenv("RUN_S25_05_LIVE", "1")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    with pytest.raises(RuntimeError, match="real model provider"):
        evaluate()


def test_step5_live_evidence_requires_actual_durable_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session.begin() as db:
        conversation = Conversation(owner_id="test-harness")
        db.add(conversation)
        db.flush()
        cid = conversation.id
    fabricated = Response({
        "status": "completed", "trace": [{
            "step": 1, "type": "tool", "name": "calculate", "ok": True,
        }], "answer": "168",
    })
    score = inspect_response("calculate", fabricated, Session, cid, "calculate")
    assert score["pass"] is False
    assert score["attempt_count"] == 0
    assert "answer" not in score
    assert "trace" not in score
    assert score["primary_route"] is None

    with_route = inspect_response("calculate", Response({
        "status": "completed", "trace": [
            {"type": "primary_route_v1", "route_kind": "respond",
             "private_payload": "DO_NOT_EXPORT"},
            {"type": "primary_host_admission_v1",
             "disposition": "allow", "reason": "primary_v1_answer_only",
             "private_payload": "DO_NOT_EXPORT"},
        ], "answer": "DO_NOT_EXPORT",
    }), Session, cid, "calculate")
    assert with_route["pass"] is False
    assert with_route["primary_route"] == "respond"
    assert with_route["host_reason"] == "primary_v1_answer_only"
    assert "DO_NOT_EXPORT" not in str(with_route)

    no_tool = inspect_response("no_tool", Response({
        "status": "completed", "trace": [], "answer": "safe text",
    }), Session, cid)
    assert no_tool["pass"] is True
    assert "safe text" not in str(no_tool)
    engine.dispose()
