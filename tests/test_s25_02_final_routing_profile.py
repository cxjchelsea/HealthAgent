"""S25-02 Final Step 3: real-model default, explicit rollback and one primary owner."""
import json
import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.config import Settings
from agent.m5_unified_route import propose_new_plan_route, SavedPlanRouteRefused
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


def test_real_provider_auto_defaults_to_unified():
    s = Settings(model_provider="ollama_proxy")
    assert s.effective_routing_profile == "unified"
    assert (s.route_admission_mode, s.understanding_strategy,
            s.m5_unified_route_enabled, s.m5_first_route_enabled) == (
                "guarded", "structured", True, False)


def test_default_fake_keeps_existing_offline_behavior():
    s = Settings()
    assert s.model_provider == "fake"
    assert s.effective_routing_profile == "legacy"
    assert not s.m5_unified_route_enabled


def test_explicit_legacy_profile_is_safe_rollback():
    s = Settings(model_provider="ollama_proxy", routing_profile="legacy")
    assert s.effective_routing_profile == "legacy"
    assert s.understanding_strategy == "direct"
    assert not s.m5_unified_route_enabled


def test_explicit_unified_even_with_fake_is_testable():
    s = Settings(model_provider="fake", routing_profile="unified")
    assert s.effective_routing_profile == "unified"
    assert s.m5_unified_route_enabled


@pytest.mark.parametrize("kwargs", [
    {"routing_profile": "unified", "understanding_strategy": "direct"},
    {"routing_profile": "unified", "m5_unified_route_enabled": False},
    {"routing_profile": "unified", "m5_first_route_enabled": True},
    {"routing_profile": "legacy", "route_admission_mode": "guarded"},
])
def test_mixed_explicit_profile_fails_startup(kwargs):
    with pytest.raises(ValidationError, match="routing_profile_conflicts"):
        Settings(model_provider="ollama_proxy", **kwargs)


def test_legacy_compatibility_settings_remain_explicitly_available():
    s = Settings(model_provider="ollama_proxy",
                 route_admission_mode="guarded",
                 understanding_strategy="structured",
                 m5_unified_route_enabled=False)
    assert s.effective_routing_profile == "compatibility"


def valid_understanding():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "继续既有计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    }, ensure_ascii=False)


def test_missing_saved_plan_uses_single_primary_no_legacy_reclassification(
        tmp_path, monkeypatch):
    counts = {"primary": 0, "old_plan": 0, "missing_review": 0}
    async def primary(*args, **kwargs):
        counts["primary"] += 1
        return PlanRequest(intent="resume", steps=[])

    async def old_plan(*args, **kwargs):
        counts["old_plan"] += 1
        raise AssertionError("legacy classifier must not replace M5")

    async def missing_review(*args, **kwargs):
        counts["missing_review"] += 1
        raise AssertionError("unified missing-plan primary must not need a second classifier")

    async def complete(self, *, model, messages, response_schema=None):
        return valid_understanding()

    async def policy(message, interpretation):
        return "respond_only"

    monkeypatch.setattr("apps.api.main.propose_new_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_intent", old_plan)
    monkeypatch.setattr("apps.api.main.review_missing_plan_reference", missing_review)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'missing.db'}",
                        model_provider="fake", routing_profile="unified")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        details = client.get("/model").json()
        assert details["routing_profile"] == "unified"
        assert details["understanding_strategy"] == "structured"
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请继续执行之前保存的计划",
            "client_request_id": "final-missing-0001"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["termination_reason"] == "plan_not_found"
        assert body["steps"] == 0
        assert counts == {"primary": 1, "old_plan": 0, "missing_review": 0}


def test_missing_plan_primary_is_host_source_bound_and_nonexecuting():
    class Provider:
        async def complete(self, **kwargs):
            return json.dumps({
                "route": "plan_missing",
                "quote": "继续执行之前保存的计划",
                "steps": [],
            }, ensure_ascii=False)

    message = "请继续执行之前保存的计划"
    result = asyncio.run(propose_new_plan_route(Provider(), "model", message))
    assert result.intent == "resume"
    assert result.steps == []

    class FabricatedProvider:
        async def complete(self, **kwargs):
            return json.dumps({
                "route": "plan_missing", "quote": "请执行不存在的计划",
                "steps": [],
            }, ensure_ascii=False)

    with pytest.raises(SavedPlanRouteRefused):
        asyncio.run(propose_new_plan_route(FabricatedProvider(), "model", message))
