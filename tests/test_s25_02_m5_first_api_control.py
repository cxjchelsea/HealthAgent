"""M5 FIRST scoped FastAPI routing regression with injected semantic reviewers.

Uses real FastAPI + SQLite, but no live model or business tool execution.
"""
import json

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.plan_intent import PlanRequest
from agent.plan_revision_review import RevisionProposal
from apps.api.main import create_app
from services.db import ConversationPlan, database


def test_m5_nonfirst_keeps_revision_route_and_bad_first_fences(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm5_control.db'}",
        model_provider="fake",
        route_admission_mode="guarded",
        understanding_strategy="structured",
        m5_first_route_enabled=True,
    )
    decision = {"primary": None, "legacy_calls": 0, "legacy_intent": "other"}

    async def no_cancel(*args, **kwargs):
        return False

    async def first_primary(*args, **kwargs):
        return decision["primary"]

    async def legacy_plan(*args, **kwargs):
        decision["legacy_calls"] += 1
        return PlanRequest(intent=decision["legacy_intent"], steps=[])

    async def uncertain_edit(*args, **kwargs):
        return RevisionProposal(intent="other", edit_requested=True)

    monkeypatch.setattr("apps.api.main.review_plan_cancellation", no_cancel)
    monkeypatch.setattr("apps.api.main.propose_first_route", first_primary)
    monkeypatch.setattr("apps.api.main.review_plan_intent", legacy_plan)
    monkeypatch.setattr("apps.api.main.review_plan_revision", uncertain_edit)

    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        checkpoint = {
            "revision": 1,
            "next_step_id": 3,
            "steps": [
                {"id": 1, "goal": "计算 3*4", "status": "pending",
                 "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
                {"id": 2, "goal": "计算 5*6", "status": "pending",
                 "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}},
            ],
        }
        with Session.begin() as db:
            db.add(ConversationPlan(
                conversation_id=cid, snapshot=json.dumps(checkpoint, ensure_ascii=False)))
        url = f"/conversations/{cid}/agent/messages"

        response = client.post(url, json={
            "content": "请修改计划第二步，原来的计算改成新的计算",
            "client_request_id": "m5-nonfirst-001",
        })
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "plan_revision_conflict"
        assert decision["legacy_calls"] == 1
        assert not any(x.get("type") == "tool" for x in response.json()["trace"])

        decision["primary"] = False
        decision["legacy_calls"] = 0
        rejected = client.post(url, json={
            "content": "请只执行第一步",
            "client_request_id": "m5-bad-evidence-002",
        })
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["termination_reason"] == "m5_primary_scope_unverified"
        assert decision["legacy_calls"] == 0
        assert not any(x.get("type") == "tool" for x in rejected.json()["trace"])

        # A legacy disagreement may not revive FIRST when primary says non-FIRST.
        decision["primary"] = None
        decision["legacy_intent"] = "execute_first"
        decision["legacy_calls"] = 0
        disagreement = client.post(url, json={
            "content": "请只执行第一步",
            "client_request_id": "m5-legacy-disagreement-003",
        })
        assert disagreement.status_code == 200, disagreement.text
        assert disagreement.json()["termination_reason"] == "m5_primary_scope_unverified"
        assert decision["legacy_calls"] == 1
        assert not any(x.get("type") == "tool" for x in disagreement.json()["trace"])
