"""AC1 v1 canary: one semantic route owner, useful non-tool outcomes.

Legacy Unified remains unchanged until WP2 admission / WP3 effects are done.
This suite intentionally fails if a second semantic reviewer runs on the v1 path.
"""
from fastapi.testclient import TestClient
import pytest

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app


@pytest.mark.parametrize("kind,message,quote,question,expected", [
    ("respond", "解释一下状态机", "状态机", None, "model_final"),
    ("clarify", "帮我处理一下", "处理一下", "具体需要处理哪项任务？",
     "primary_v1_clarification_required"),
    ("reject", "执行未经授权的操作", "未经授权", None,
     "primary_v1_route_rejected"),
])
def test_single_primary_owner_handles_nonplan_routes(
        tmp_path, monkeypatch, kind, message, quote, question, expected):
    counts = {"primary": 0, "answer": 0}
    async def primary(*args, **kwargs):
        counts["primary"] += 1
        signal = RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": kind, "quote": quote,
            "execution_scope": "none", "effect_intent": "none",
            "clarification_question": question})
        return bind_route_signal(signal, message,
                                 saved_plan_present=kwargs["saved_plan_present"],
                                 pending_action=kwargs["pending_action"],
                                 pending_origin_verified=kwargs["pending_origin_verified"])

    async def prohibited(*args, **kwargs):
        raise AssertionError("second semantic owner must not run")

    class AnswerOnlyLoop:
        def __init__(self, *args, **kwargs):
            assert kind == "respond"

        async def run(self, *args, **kwargs):
            counts["answer"] += 1
            assert kwargs["execution_mode"] == "respond_only"
            assert kwargs["memory_write_consent"] is False
            assert kwargs["plan_control"] is None
            return {"run_id": "fake", "status": "completed",
                    "termination_reason": "model_final",
                    "answer": "这是状态转移规则。",
                    "trace": [{"step": 0, "type": "final"}],
                    "steps": 0}

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.AgentLoop", AnswerOnlyLoop)
    for name in ("propose_new_plan_route", "propose_saved_plan_route",
                 "review_plan_intent", "review_plan_cancellation",
                 "review_plan_revision", "classify_pending_reply",
                 "review_pending_reference_lookup", "review_unsupported_capability",
                 "review_read_only_clarification", "reconcile_execution_policy",
                 "decide_execution_policy_detail", "review_plan_creation_reference",
                 "review_missing_plan_reference", "review_plan_execution_authorization",
                 "review_plan_execution_scope", "propose_first_route"):
        monkeypatch.setattr("apps.api.main." + name, prohibited)

    config = Settings(database_url=f"sqlite:///{tmp_path / 'ac1.db'}",
                      model_provider="fake", routing_profile="unified",
                      primary_route_v1_canary_enabled=True)
    with TestClient(create_app(config)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message, "client_request_id": "v1-ac1-nonplan-001"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["termination_reason"] == expected
        if kind == "clarify":
            assert result["answer"] == question
        if kind == "respond":
            assert result["status"] == "completed"
        labels = [entry for entry in result["trace"]
                  if entry.get("type") == "primary_route_v1"]
        assert len(labels) == 1
        assert labels[0]["route_kind"] == kind
        assert labels[0]["schema_version"] == "primary-route-v1"
        assert not any(entry.get("type") == "tool" for entry in result["trace"])
    assert counts == {"primary": 1, "answer": int(kind == "respond")}
