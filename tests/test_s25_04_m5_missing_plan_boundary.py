"""Missing saved-plan boundary across current Primary and fake M5 compatibility.

The real-provider Unified route has one bound Primary owner. A model
cannot declare that a plan is absent as authority to execute a plan.
The separate fake-provider M5 regression below remains as a fixture.
"""
import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app


@pytest.mark.parametrize("message,route_kind,expected_missing", [
    ("请查看我以前列的待办事项", "ordinary_tool", False),
    ("请在参考目录查询 MCP 的定义", "ordinary_tool", False),
    ("请继续执行之前保存的计划", "plan_missing", True),
])
def test_missing_plan_proposal_requires_independent_review(
        tmp_path, monkeypatch, message, route_kind, expected_missing):
    # The single Primary chooses a route. The Host, not a second model,
    # validates whether a saved checkpoint exists.
    calls = {"primary": 0}

    async def primary(*args, **kwargs):
        calls["primary"] += 1
        assert kwargs["saved_plan_present"] is False
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": route_kind, "quote": message,
            "execution_scope": "none",
            "effect_intent": ("read_only" if route_kind == "ordinary_tool"
                              else "none"),
        }), message, saved_plan_present=False, pending_action=None)

    def legacy_forbidden(*args, **kwargs):
        raise AssertionError("Primary Unified must not use old missing-plan review")

    class ReadOnlyLoop:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, goal, **kwargs):
            assert kwargs["execution_mode"] == "read_only"
            return {"run_id": "test", "status": "completed",
                    "answer": "普通只读请求", "trace": [], "steps": 0}

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.review_missing_plan_reference", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.AgentLoop", ReadOnlyLoop)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path/'missing.db'}",
        model_provider="ollama_proxy", routing_profile="unified",
    )
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message, "client_request_id": "s25-04-plan-boundary-001",
        })
    assert response.status_code == 200, response.text
    assert calls["primary"] == 1
    assert (response.json().get("termination_reason")
            == "primary_v1_missing_plan_status") is expected_missing


def test_invalid_m5_create_stays_fenced(tmp_path, monkeypatch):
    from agent.m5_unified_route import SavedPlanRouteRefused

    async def malformed_primary(*args, **kwargs):
        raise SavedPlanRouteRefused("m5_create_invalid_step_count")

    monkeypatch.setattr("apps.api.main.propose_new_plan_route", malformed_primary)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path/'refusal.db'}",
        model_provider="fake", routing_profile="unified",
    )
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请创建两步可执行计划，但不要立即执行",
            "client_request_id": "s25-04-malformed-primary",
        })
    assert response.status_code == 200, response.text
    obj = response.json()
    assert obj.get("termination_reason") == "m5_unified_create_refused"
    assert any(x.get("type") == "m5_unified_fenced"
               for x in obj.get("trace", []))
