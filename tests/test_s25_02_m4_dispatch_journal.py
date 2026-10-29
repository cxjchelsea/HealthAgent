"""Durable intent/receipt/commit tri-state and no-repeat invariants."""
import json
import pytest
from sqlalchemy import select
from services.db import (Base, Conversation, Turn, PlanToolDispatch, database)
from services.plan_dispatch import PlanDispatchJournal, DispatchConflict


def _setup(tmp_path):
    engine, Session = database(f"sqlite:///{tmp_path / 'dispatch.db'}")
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        c = Conversation(owner_id="test")
        db.add(c)
        db.flush()
        cid = c.id
        t = Turn(conversation_id=cid, request_id="r0000001", state="running")
        db.add(t)
        db.flush()
        tid = t.id
    return engine, Session, cid, tid


def test_intent_before_dispatch_and_checkpoint_atomic(tmp_path):
    engine, Session, cid, tid = _setup(tmp_path)
    try:
        journal = PlanDispatchJournal(Session, cid, tid)
        dispatch_id = journal.begin(1, "calculate", {"expression": "4*7"})
        with Session() as db:
            row = db.get(PlanToolDispatch, dispatch_id)
            assert row.status == "dispatch_unknown"
            assert row.receipt_json is None
        with pytest.raises(DispatchConflict, match="unreconciled_dispatch"):
            journal.begin(1, "calculate", {"expression": "4*7"})
        journal.record_receipt(dispatch_id, {"ok": True, "value": 28})
        with Session() as db:
            row = db.get(PlanToolDispatch, dispatch_id)
            assert row.status == "observed"
            assert json.loads(row.receipt_json)["value"] == "28"
        with pytest.raises(DispatchConflict, match="unreconciled_dispatch"):
            journal.begin(1, "calculate", {"expression": "4*7"})
        with Session.begin() as db:
            journal.checkpoint_in_session(db, {"steps": [
                {"id": 1, "status": "completed",
                 "expected_tool": "calculate",
                 "expected_arguments": {"expression": "4*7"},
                 "evidence_tool": "calculate"}]})
            db.get(Turn, tid).state = "completed"
        with Session() as db:
            assert db.get(PlanToolDispatch, dispatch_id).status == "checkpointed"
        with pytest.raises(DispatchConflict, match="turn_not_running"):
            journal.begin(2, "calculate", {"expression": "9+3"})
    finally:
        engine.dispose()


def test_crashed_dispatch_remains_unknown_after_new_journal_object(tmp_path):
    engine, Session, cid, tid = _setup(tmp_path)
    try:
        first = PlanDispatchJournal(Session, cid, tid)
        first.begin(1, "calculate", {"expression": "4*7"})
        recovered = PlanDispatchJournal(Session, cid, tid)
        with pytest.raises(DispatchConflict, match="unreconciled_dispatch"):
            recovered.begin(1, "calculate", {"expression": "4*7"})
        with Session() as db:
            assert db.scalar(select(PlanToolDispatch.status)) == "dispatch_unknown"
    finally:
        engine.dispose()


def test_guarded_http_plan_tool_receipt_and_checkpoint_are_persisted(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from agent.config import Settings
    from agent.plan_intent import PlanRequest
    from agent.understanding import UnderstandingResult, UnderstandingService
    from agent.execution_policy import ExecutionPolicyDecision
    from apps.api.main import create_app
    from services.db import ConversationPlan

    import apps.api.main as api

    async def understood(self, context):
        return {"result": UnderstandingResult.model_validate({
            "schema_version": "s25-01-v1", "status": "understood",
            "goal": {"objective": "两步计算", "subgoals": [], "constraints": [],
                     "success_criteria": []},
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
        }), "attempts": []}

    async def intent(provider, model, message, existing):
        if existing:
            return PlanRequest(intent="execute_first")
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "计算 4*7", "expected_tool": "calculate",
             "expected_arguments": {"expression": "4*7"}},
            {"goal": "计算 9+3", "expected_tool": "calculate",
             "expected_arguments": {"expression": "9+3"}}])

    async def allow(*args, **kwargs):
        return True

    async def policy(*args, **kwargs):
        return ExecutionPolicyDecision(mode="read_only", target="tool_action")

    monkeypatch.setattr(UnderstandingService, "understand", understood)
    monkeypatch.setattr(api, "review_plan_intent", intent)
    monkeypatch.setattr(api, "review_plan_creation_reference", allow)
    monkeypatch.setattr(api, "review_plan_execution_authorization", allow)
    monkeypatch.setattr(api, "decide_execution_policy_detail", policy)

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'http_journal.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured",
                        route_admission_mode="guarded", route_shadow_enabled=False)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        created = client.post(endpoint, json={
            "content": "创建两步计划：第一步计算 4*7，第二步计算 9+3，只保存不执行",
            "client_request_id": "m4-journal-create"})
        assert created.status_code == 200, created.text
        first = client.post(endpoint, json={
            "content": "只执行已保存计划的第一步，其余步骤不执行",
            "client_request_id": "m4-journal-first"})
        assert first.status_code == 200, first.text
        assert first.json()["plan"][0]["status"] == "completed"
    engine, Session = database(settings.database_url)
    try:
        with Session() as db:
            entries = list(db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)))
            assert len(entries) == 1
            assert entries[0].tool_name == "calculate"
            assert entries[0].status == "checkpointed"
            receipt = json.loads(entries[0].receipt_json)
            assert receipt["ok"] is True and receipt["value"] == "28"
            snapshot = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert [x["status"] for x in snapshot["steps"]] == ["completed", "pending"]
    finally:
        engine.dispose()
