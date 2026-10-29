"""M5 regression: trustworthy pending clarifications take priority over new routes."""
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app
from services.db import ConversationPlan, Message, Turn, database


def understood():
    return json.dumps({
        "schema_version": "s25-01-v1",
        "status": "understood",
        "goal": {"objective": "查询术语", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "clarify_response", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    })


@pytest.mark.parametrize("with_saved_plan", [False, True])
def test_pending_origin_is_not_sent_to_fresh_plan_primary(
        tmp_path, monkeypatch, with_saved_plan):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'pending.db'}",
        model_provider="ollama_proxy",
        understanding_strategy="structured",
        route_admission_mode="guarded",
        m5_unified_route_enabled=True,
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("pending reply must not be freshly classified as a plan")

    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()

    async def pending_answer(*args, **kwargs):
        return "answer"

    async def old_plan(*args, **kwargs):
        return PlanRequest(intent="other", steps=[])

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", old_plan)
    monkeypatch.setattr("apps.api.main.classify_pending_reply", pending_answer)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.execute_tool",
                        lambda *a, **kw: {"ok": True, "definition": "test definition"})

    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        original = None
        with Session.begin() as db:
            pending = Message(conversation_id=cid, role="assistant",
                              content="请提供要查询的词条", status="partial")
            db.add(pending)
            db.flush()
            db.add(Turn(conversation_id=cid, request_id="m5-pending-prior-001",
                        state="partial", assistant_message_id=pending.id,
                        failure_code="needs_input:mcp_reference_lookup"))
            if with_saved_plan:
                original = {"revision": 1, "next_step_id": 3, "steps": [
                    {"id": 1, "goal": "计算3*4", "status": "pending",
                     "expected_tool": "calculate",
                     "expected_arguments": {"expression": "3*4"}},
                    {"id": 2, "goal": "计算5*6", "status": "pending",
                     "expected_tool": "calculate",
                     "expected_arguments": {"expression": "5*6"}},
                ]}
                db.add(ConversationPlan(
                    conversation_id=cid,
                    snapshot=json.dumps(original, ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "RAG", "client_request_id": "m5-pending-followup-001"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["termination_reason"] == "verified_clarification_execution"
        assert payload["steps"] == 1
        with Session() as db:
            saved = db.get(ConversationPlan, cid)
            if with_saved_plan:
                assert json.loads(saved.snapshot) == original
            else:
                assert saved is None
