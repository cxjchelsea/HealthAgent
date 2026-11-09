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


def test_step5_evidence_reader_engine_disposed_on_success_and_failure(monkeypatch):
    from evals import s25_05_step5_live as harness

    engines = []
    class Engine:
        def __init__(self):
            self.disposed = False
        def dispose(self):
            self.disposed = True
    def factory(url):
        engine = Engine()
        engines.append(engine)
        return engine, object()
    monkeypatch.setattr(harness, "database", factory)

    with harness.isolated_read_sessions("sqlite:///temporary.sqlite") as Session:
        assert Session is not None
        assert not engines[-1].disposed
    assert engines[-1].disposed

    with pytest.raises(RuntimeError, match="intentional"):
        with harness.isolated_read_sessions("sqlite:///temporary.sqlite"):
            raise RuntimeError("intentional")
    assert len(engines) == 2
    assert all(engine.disposed for engine in engines)


def test_step5_temp_sqlite_can_be_removed_after_reader_pool_is_disposed(tmp_path):
    from evals.s25_05_step5_live import isolated_read_sessions
    from sqlalchemy import text
    dbpath = tmp_path / "s25-05-windows-disposal.sqlite"
    url = "sqlite:///" + dbpath.as_posix()
    with isolated_read_sessions(url) as Session:
        with Session() as db:
            db.execute(text("CREATE TABLE acceptance_probe (id INTEGER)"))
            db.commit()
        assert dbpath.exists()
    dbpath.unlink()
    assert not dbpath.exists()


def test_live_plan_memory_phrase_mode_keeps_default_suite_unchanged():
    from evals.s25_05_step5_live import PLAN_CASES, PLAN_MEMORY_PHRASE_CASES

    assert [x[0] for x in PLAN_MEMORY_PHRASE_CASES] == [
        "plan_create", "plan_first", "plan_resume"]
    assert "保存的" in PLAN_MEMORY_PHRASE_CASES[1][1]
    assert "之前保存的" in PLAN_MEMORY_PHRASE_CASES[2][1]
    assert "保存的" not in PLAN_CASES[1][1]
    assert PLAN_CASES[0] == PLAN_MEMORY_PHRASE_CASES[0]
