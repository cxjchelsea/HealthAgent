"""M5 unified CREATE/ordinary route: deterministic source and Host admission."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.m5_unified_route import propose_new_plan_route, SavedPlanRouteRefused
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


MESSAGE = "请建立两步计划：第一步计算3*4，第二步计算5*6，只保存，不执行。"


class Provider:
    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return json.dumps(self.raw, ensure_ascii=False)


def payload(message=MESSAGE):
    root = "建立两步计划"
    steps = []
    for goal, value in [("计算 3*4", "3*4"), ("计算 5*6", "5*6")]:
        start = message.index(value)
        steps.append({
            "goal": goal, "tool_name": "calculate",
            "arguments": {"expression": value},
            "quote": value, "start": start, "end": start + len(value),
        })
    start = message.index(root)
    return {"route": "plan_create", "quote": root, "start": start,
            "end": start + len(root), "steps": steps}


def test_create_has_one_model_route_and_verified_step_arguments():
    provider = Provider(payload())
    result = asyncio.run(propose_new_plan_route(provider, "model", MESSAGE))
    assert provider.calls == 1
    assert result.intent == "plan_only"
    assert [x["expected_arguments"]["expression"] for x in result.steps] == ["3*4", "5*6"]


def test_source_or_arguments_mismatch_fails_closed():
    bad = payload()
    bad["steps"][0]["arguments"] = {"expression": "8*8"}
    with pytest.raises(SavedPlanRouteRefused):
        asyncio.run(propose_new_plan_route(Provider(bad), "model", MESSAGE))
    # Numerical offsets from the model are not source authority:
    # Host recovers the only exact occurrence from its current request.
    bad = payload()
    bad["steps"][1]["start"] += 1
    assert asyncio.run(propose_new_plan_route(
        Provider(bad), "model", MESSAGE)).intent == "plan_only"
    # But missing or ambiguous exact quote remains fail-closed.
    bad = payload()
    bad["steps"][1]["quote"] = "not in user request"
    with pytest.raises(SavedPlanRouteRefused):
        asyncio.run(propose_new_plan_route(Provider(bad), "model", MESSAGE))
    bad = payload()
    bad["quote"] = "计算"
    with pytest.raises(SavedPlanRouteRefused):
        asyncio.run(propose_new_plan_route(Provider(bad), "model", MESSAGE))


def test_ordinary_does_not_create_plan():
    message = "请计算3*4"
    start = message.index("计算")
    provider = Provider({"route": "other", "quote": "计算",
                         "start": start, "end": start + 2, "steps": []})
    result = asyncio.run(propose_new_plan_route(provider, "model", message))
    assert provider.calls == 1
    assert result.intent == "other"
    assert result.steps == []


def valid_understanding():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "保存计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    })


def test_api_create_uses_primary_without_legacy_intent_or_reference(tmp_path, monkeypatch):
    counters = {"primary": 0, "legacy": 0, "ref": 0}

    async def primary(*args, **kwargs):
        counters["primary"] += 1
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "计算 3*4", "expected_tool": "calculate",
             "expected_arguments": {"expression": "3*4"}},
            {"goal": "计算 5*6", "expected_tool": "calculate",
             "expected_arguments": {"expression": "5*6"}},
        ])

    async def legacy(*args, **kwargs):
        counters["legacy"] += 1
        return PlanRequest(intent="other")

    async def reference(*args, **kwargs):
        counters["ref"] += 1
        return False

    async def fake_complete(self, *, model, messages, response_schema=None):
        return valid_understanding()

    monkeypatch.setattr("apps.api.main.propose_new_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_intent", legacy)
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", reference)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)

    settings = Settings(database_url=f"sqlite:///{tmp_path/'create.db'}",
                        model_provider="ollama_proxy", route_admission_mode="guarded",
                        understanding_strategy="structured",
                        m5_unified_route_enabled=True)
    async def policy(message, interpretation):
        return "read_only"

    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "m5-unified-create-001",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["termination_reason"] == "plan_created_pending_execution"
        assert [s["status"] for s in data["plan"]] == ["pending", "pending"]
        assert not any(t.get("type") == "tool" for t in data["trace"])
        assert counters == {"primary": 1, "legacy": 0, "ref": 0}


def test_api_unbound_create_never_falls_through_to_tools(tmp_path, monkeypatch):
    async def reject(*args, **kwargs):
        raise SavedPlanRouteRefused("bad span")

    async def legacy(*args, **kwargs):
        raise AssertionError("legacy classifier should not run after refusal")

    monkeypatch.setattr("apps.api.main.propose_new_plan_route", reject)
    monkeypatch.setattr("apps.api.main.review_plan_intent", legacy)

    settings = Settings(database_url=f"sqlite:///{tmp_path/'deny.db'}",
                        model_provider="fake", route_admission_mode="guarded",
                        understanding_strategy="structured",
                        m5_unified_route_enabled=True)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "m5-unified-create-denied-001",
        })
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "m5_unified_create_refused"
        assert not any(t.get("type") == "tool" for t in response.json()["trace"])


def test_quote_only_without_model_offsets_binds_on_host():
    raw = payload()
    raw.pop("start")
    raw.pop("end")
    for step in raw["steps"]:
        step.pop("start")
        step.pop("end")
    assert asyncio.run(propose_new_plan_route(
        Provider(raw), "model", MESSAGE)).intent == "plan_only"


def test_ordinary_quote_only_with_unreliable_offsets_is_not_rejected():
    message = "请使用计算工具计算 19+23 的结果，不要创建计划。"
    raw = {"route": "other", "quote": "不要创建计划", "steps": [],
           "start": 0, "end": 1}
    assert asyncio.run(propose_new_plan_route(
        Provider(raw), "model", message)).intent == "other"
