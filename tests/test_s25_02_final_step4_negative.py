"""Step 4 safety boundaries for unified routing; negative paths must not dispatch."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.config import Settings
from agent.m5_unified_route import SavedPlanRouteRefused
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app
from services.db import ConversationPlan, database


def checkpoint():
    return {"revision": 1, "next_step_id": 3, "steps": [
        {"id": 1, "goal": "计算 3*4", "status": "pending",
         "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
        {"id": 2, "goal": "计算 5*6", "status": "pending",
         "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}},
    ]}


def understood():
    return json.dumps({"schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "讨论数学", "subgoals": [], "constraints": [],
                 "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}},
        ensure_ascii=False)


def save_plan(settings, cid):
    _, Session = database(settings.database_url)
    with Session.begin() as db:
        db.add(ConversationPlan(conversation_id=cid,
            snapshot=json.dumps(checkpoint(), ensure_ascii=False)))


def test_saved_plan_other_cannot_reenter_legacy_plan_classifiers(tmp_path, monkeypatch):
    calls = {"primary": 0}
    async def primary(*args, **kwargs):
        calls["primary"] += 1
        return "other"

    async def legacy(*args, **kwargs):
        raise AssertionError("saved-plan M5 other cannot invoke legacy classifier")

    async def complete(self, *, model, messages, response_schema=None):
        return understood()

    async def policy(message, interpretation):
        return "respond_only"

    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_intent", legacy)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", legacy)
    monkeypatch.setattr("apps.api.main.review_plan_revision", legacy)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'other.db'}",
        model_provider="fake", routing_profile="unified")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        save_plan(settings, cid)
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "你好，这只是普通聊天", "client_request_id": "step4-saved-other-01"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("termination_reason") not in {
            "plan_cancelled", "plan_revised_pending_execution", "plan_execution_completed"}
        assert body.get("plan_revision", 1) in {0, 1}
        assert calls["primary"] == 1
        _, Session = database(settings.database_url)
        with Session() as db:
            record = db.get(ConversationPlan, cid)
            assert record is not None
            assert json.loads(record.snapshot)["revision"] == 1


@pytest.mark.parametrize("with_plan", [False, True])
def test_primary_source_refusal_prevents_legacy_and_tools(
        tmp_path, monkeypatch, with_plan):
    async def refused(*args, **kwargs):
        raise SavedPlanRouteRefused("m5_quote_not_found")

    async def legacy(*args, **kwargs):
        raise AssertionError("refused primary cannot delegate to legacy")

    monkeypatch.setattr("apps.api.main.propose_new_plan_route", refused)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", refused)
    monkeypatch.setattr("apps.api.main.review_plan_intent", legacy)
    settings = Settings(database_url=f"sqlite:///{tmp_path / ('saved.db' if with_plan else 'new.db')}",
                        model_provider="fake", routing_profile="unified")
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        if with_plan:
            save_plan(settings, cid)
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请直接执行计划", "client_request_id": "step4-source-refused-01"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["steps"] == 0
        assert body["termination_reason"] in {
            "m5_unified_binding_refused", "m5_unified_create_refused"}
        assert not any(ev.get("type") == "tool" for ev in body["trace"])
        _, Session = database(settings.database_url)
        with Session() as db:
            persisted = db.get(ConversationPlan, cid)
            assert (persisted is not None) == with_plan


@pytest.mark.parametrize("with_plan", [False, True])
def test_invalid_primary_schema_is_provider_error_not_action(
        tmp_path, monkeypatch, with_plan):
    from agent.understanding import UnderstandingError
    async def invalid(*args, **kwargs):
        raise UnderstandingError("m5_unified_primary_invalid")

    monkeypatch.setattr("apps.api.main.propose_new_plan_route", invalid)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", invalid)
    settings = Settings(database_url=f"sqlite:///{tmp_path / ('schema-saved.db' if with_plan else 'schema-new.db')}",
                        model_provider="fake", routing_profile="unified")
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        if with_plan:
            save_plan(settings, cid)
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请执行保存的计划", "client_request_id": "step4-invalid-primary-01"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "m5_unified_primary_invalid"
        _, Session = database(settings.database_url)
        with Session() as db:
            persisted = db.get(ConversationPlan, cid)
            assert (persisted is not None) == with_plan


def test_invalid_profile_cannot_be_constructed_from_environment(monkeypatch):
    monkeypatch.setenv("ROUTING_PROFILE", "unified")
    monkeypatch.setenv("ROUTE_ADMISSION_MODE", "legacy")
    with pytest.raises(ValidationError, match="routing_profile_conflicts"):
        Settings(_env_file=None, model_provider="ollama_proxy")
