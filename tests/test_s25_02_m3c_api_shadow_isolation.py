"""Formal HTTP API regression: shadow cannot change a committed legacy outcome."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.plan_intent import PlanRequest
from agent.understanding import UnderstandingResult
from apps.api.main import create_app
from services.db import Turn, database


def test_shadow_exception_preserves_committed_turn_and_does_not_repeat_tool(tmp_path, monkeypatch):
    import apps.api.main as api

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'shadow_fault.db'}",
        model_provider="ollama_proxy",
        understanding_strategy="structured",
        route_shadow_enabled=True,
    )
    observed = {"legacy_runs": 0, "shadow_runs": 0}

    async def understand(self, context):
        return {"result": UnderstandingResult.model_validate({
            "schema_version": "s25-01-v1",
            "status": "understood",
            "goal": None,
            "change": {"kind": "chat", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
        }), "attempts": []}

    async def plan_intent(*args, **kwargs):
        return PlanRequest(intent="other", steps=[])

    async def policy(*args, **kwargs):
        from agent.execution_policy import ExecutionPolicyDecision
        return ExecutionPolicyDecision(mode="read_only", target="tool_action")

    async def run(self, message, **kwargs):
        observed["legacy_runs"] += 1
        return {"run_id": "legacy", "status": "completed",
                "answer": "42", "steps": 1,
                "trace": [{"type": "tool", "name": "calculate", "ok": True}]}

    async def broken_shadow(*args, **kwargs):
        observed["shadow_runs"] += 1
        raise RuntimeError("sensitive model failure detail")

    monkeypatch.setattr(api.UnderstandingService, "understand", understand)
    monkeypatch.setattr(api, "review_plan_intent", plan_intent)
    monkeypatch.setattr(api, "decide_execution_policy_detail", policy)
    monkeypatch.setattr(api.AgentLoop, "run", run)
    monkeypatch.setattr(api, "evaluate_shadow", broken_shadow)

    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "使用计算工具计算 6*7",
            "client_request_id": "shadow-fault-test-001",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "completed"
        assert data["answer"] == "42"
        assert data["route_shadow"]["state"] == "unavailable"
        assert data["route_shadow"]["error_class"] == "ShadowEvaluationError"
        assert data["route_shadow"]["execution_authorized"] is False
        assert data["route_shadow"]["diagnostic_model_calls"] == 1
        assert data["route_shadow"]["diagnostic_elapsed_ms"] >= 0
        assert "sensitive model failure detail" not in response.text
        assert observed == {"legacy_runs": 1, "shadow_runs": 1}

    engine, Session = database(settings.database_url)
    try:
        with Session() as db:
            turns = list(db.scalars(select(Turn).where(Turn.conversation_id == cid)))
            assert len(turns) == 1
            assert turns[0].state == "completed"
    finally:
        engine.dispose()
