"""S25-03 Step 6-A: bounded M4 receipt, provenance and latest history."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from apps.api.main import create_app
from services.db import (
    Conversation, Message, Turn, AgentAttempt, PlanToolDispatch,
    AgentStateTransition, database,
)
from services.migrate import migrate
from services.plan_dispatch import PlanDispatchJournal
from services.s25_03_projection import project_finalized_turn
from services.task_state import AgentTaskStateStore


@pytest.mark.parametrize("tool,args,observed,expected,absent", [
    ("calculate", {"expression": "6*7"}, {"ok": True, "value": 42},
     {"value": "42"}, ()),
    ("utc_time", {}, {"ok": True, "utc": "2026-11-10T00:00:00Z"},
     {"utc": "2026-11-10T00:00:00Z"}, ()),
    ("mcp_reference_lookup", {"term": "RAG"},
     {"ok": True, "source": "trusted-reference", "definition": "retrieval augmented generation",
      "secret": "DO_NOT_PERSIST"}, {"source": "trusted-reference",
      "definition": "retrieval augmented generation"}, ("secret",)),
    ("list_tasks", {}, {"ok": True, "tasks": [{"title": "synthetic"}],
                         "secret": "DO_NOT_PERSIST"},
     {"tasks": [{"title": "synthetic"}]}, ("secret",)),
    ("recall_memory", {"key": "example"},
     {"ok": True, "found": True, "value": "TOP_SECRET_MEMORY"},
     {"found": True}, ("value", "TOP_SECRET_MEMORY")),
])
def test_m4_receipt_preserves_only_bounded_tool_fields(
        tmp_path, tool, args, observed, expected, absent):
    url = "sqlite:///" + (tmp_path / "receipt.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="owner"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="t1", conversation_id="c1", assistant_message_id="m1",
                    request_id="step6-receipt", state="running"))
    journal = PlanDispatchJournal(Session, "c1", "t1")
    dispatch_id = journal.begin(1, tool, args, tool_version="legacy-plan-v1")
    journal.record_receipt(dispatch_id, observed)
    with Session() as db:
        row = db.get(PlanToolDispatch, dispatch_id)
        receipt = json.loads(row.receipt_json)
        assert row.status == "observed"
        assert receipt["ok"] is True
        assert all(receipt[k] == v for k, v in expected.items())
        assert all(token not in str(receipt) for token in absent)
    checkpoint = {"revision": 1, "steps": [{
        "id": 1, "status": "completed", "expected_tool": tool,
        "expected_arguments": args, "evidence_tool": tool,
        "tool_version": "legacy-plan-v1",
    }]}
    with Session.begin() as db:
        journal.checkpoint_in_session(db, checkpoint)
        project_finalized_turn(
            db, conversation_id="c1", turn_id="t1",
            result={"run_id": "step6-run"}, checkpoint=checkpoint,
            status="completed")
        db.get(Turn, "t1").state = "completed"
    with Session() as db:
        evidence = db.get(AgentAttempt, dispatch_id)
        assert evidence.tool_version == "legacy-plan-v1"
        result = json.loads(evidence.result_projection)
        assert all(result[k] == v for k, v in expected.items())
        assert all(token not in str(result) for token in absent)
    engine.dispose()


def test_legacy_receipt_null_version_remains_readable(tmp_path):
    url = "sqlite:///" + (tmp_path / "legacy.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="owner"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="t1", conversation_id="c1", assistant_message_id="m1",
                    request_id="step6-legacy", state="running"))
        db.add(PlanToolDispatch(
            id="legacy-d", conversation_id="c1", turn_id="t1", step_id=1,
            tool_name="calculate", tool_version=None,
            arguments_json='{"expression":"6*7"}',
            status="checkpointed", receipt_json='{"ok":true,"value":"42"}'))
    checkpoint = {"revision": 1, "steps": [{
        "id": 1, "status": "completed", "expected_tool": "calculate",
        "expected_arguments": {"expression": "6*7"},
        "evidence_tool": "calculate",
    }]}
    with Session.begin() as db:
        project_finalized_turn(db, conversation_id="c1", turn_id="t1",
                               result={}, checkpoint=checkpoint, status="completed")
    with Session() as db:
        attempt = db.get(AgentAttempt, "legacy-d")
        assert attempt.tool_version is None
        assert json.loads(attempt.result_projection)["value"] == "42"
    engine.dispose()


def test_http_task_history_latest_window_owner_isolation_and_evidence_version(tmp_path):
    cfg = Settings(
        database_url="sqlite:///" + (tmp_path / "http.db").as_posix(),
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        allow_demo_identity=False, user_tokens="token-a:alice,token-b:bob")
    with TestClient(create_app(cfg)) as client:
        _, Session = database(cfg.database_url)
        with Session.begin() as db:
            db.add(Conversation(id="c1", owner_id="alice"))
            db.add(Conversation(id="c2", owner_id="bob"))
            db.add(Message(id="m1", conversation_id="c1", role="assistant",
                           content="", status="completed"))
            db.add(Turn(id="t1", conversation_id="c1", assistant_message_id="m1",
                        request_id="step6-http", state="completed"))
            db.add(AgentAttempt(
                id="a1", conversation_id="c1", turn_id="t1",
                run_id="run", step_id=1, tool_name="utc_time",
                tool_version="legacy-plan-v1",
                arguments_digest="synthetic", status="succeeded",
                result_projection=json.dumps({
                    "ok": True, "utc": "2026-11-10T00:00:00Z"})))
            for revision in range(1, 103):
                db.add(AgentStateTransition(
                    id=f"transition-{revision:03}", conversation_id="c1",
                    task_id="synthetic-task", revision=revision,
                    entity_type="task", step_id=None,
                    from_state="partial", to_state="partial",
                    reason_code=f"revision-{revision}"))
        own = {"Authorization": "Bearer token-a"}
        stranger = {"Authorization": "Bearer token-b"}
        response = client.get("/conversations/c1/agent/task-history", headers=own)
        assert response.status_code == 200
        body = response.json()
        assert body["has_more"] is True
        assert len(body["transitions"]) == 100
        assert body["transitions"][0]["revision"] == 102
        assert body["transitions"][-1]["revision"] == 3
        evidence = client.get("/conversations/c1/turns/t1/evidence", headers=own)
        assert evidence.status_code == 200
        assert evidence.json()["attempts"][0]["tool_version"] == "legacy-plan-v1"
        assert evidence.json()["attempts"][0]["result"]["utc"] == "2026-11-10T00:00:00Z"
        for endpoint in ("agent/task-history", "agent/task-state", "turns/t1/evidence"):
            denied = client.get("/conversations/c1/" + endpoint, headers=stranger)
            assert denied.status_code == 404
        wrong_turn = client.get("/conversations/c2/turns/t1/evidence", headers=stranger)
        assert wrong_turn.status_code == 404
