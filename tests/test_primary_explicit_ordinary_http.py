"""AC3/AC4: explicitly selected calculation narrows Primary/Host tool execution.

Real HTTP Turn and AgentLoop, deterministic Primary and tool model stubs.
No live-provider inference; use separate physical evaluation for AC5.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.decision import Decision
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import ConversationTask, database


MESSAGE = "请计算 (19+23)*4"


def _primary(message: str, *, kind: str = "ordinary_tool",
             effect: str = "read_only"):
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": kind, "quote": message,
        "execution_scope": "none",
        "effect_intent": effect,
        **({"write_tool_name": "create_task",
            "write_arguments": {"title": "提交任务"}}
           if effect == "mutation_requested" else {}),
    }), message, saved_plan_present=False, pending_action=None)


def _config(tmp_path, profile):
    return Settings(database_url=f"sqlite:///{tmp_path / 'explicit.db'}",
                    model_provider="ollama_proxy", routing_profile=profile)


@pytest.mark.parametrize("profile", ["auto", "unified"])
def test_selected_calculate_executes_only_calculate_with_durable_attempt(
        tmp_path, monkeypatch, profile):
    async def primary(*args, **kwargs):
        return _primary(args[2])
    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    for name in ("propose_new_plan_route", "propose_saved_plan_route",
                 "review_plan_intent", "decide_execution_policy_detail"):
        monkeypatch.setattr("apps.api.main." + name,
                            lambda *args, **kwargs: (_ for _ in ()).throw(
                                AssertionError("must not call legacy semantics")))

    calls = []
    class ToolDecider:
        def __init__(self, settings):
            pass
        async def decide(self, messages, tools):
            names = {t["function"]["name"] for t in tools}
            calls.append(names)
            assert names == {"calculate"}, names
            if any(m.get("role") == "tool" for m in messages):
                return Decision("结果是168", [])
            return Decision("", [{
                "id": "calculate-001", "name": "calculate",
                "arguments": {"expression": "(19+23)*4"},
            }])
    monkeypatch.setattr("apps.api.main.NativeToolDecision", ToolDecider)

    cfg = _config(tmp_path, profile)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        request = {"content": MESSAGE, "client_request_id": "explicit-calc-001",
                   "selected_ordinary_operation": "calculate"}
        reply = client.post(f"/conversations/{cid}/agent/messages", json=request)
        assert reply.status_code == 200, reply.text
        assert reply.json()["status"] == "completed"
        assert any(t.get("type") == "tool" and t.get("name") == "calculate"
                   and t.get("ok") for t in reply.json()["trace"])
        assert len(calls) >= 2
        replay = client.post(f"/conversations/{cid}/agent/messages", json=request)
        assert replay.status_code == 409


@pytest.mark.parametrize("profile", ["auto", "unified"])
@pytest.mark.parametrize("route_kind", ["respond", "reject"])
def test_selected_calculate_does_not_override_primary_route(
        tmp_path, monkeypatch, profile, route_kind):
    async def primary(*args, **kwargs):
        return _primary(args[2], kind=route_kind, effect="none")
    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.AgentLoop",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("disagreed route must not execute")))
    cfg = _config(tmp_path, profile)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "explicit-mismatch-001",
            "selected_ordinary_operation": "calculate"})
        assert reply.status_code == 200, reply.text
        # Host restrict is authoritative and terminates before the
        # selected-operation mismatch check; both prohibit execution.
        expected = ("primary_v1_route_rejected" if route_kind == "reject"
                    else "explicit_ordinary_primary_mismatch")
        assert reply.json()["termination_reason"] == expected
        assert reply.json()["steps"] == 0


@pytest.mark.parametrize("profile", ["auto", "unified"])
def test_selected_operation_cannot_create_task_or_memory(
        tmp_path, monkeypatch, profile):
    async def primary(*args, **kwargs):
        message = args[2]
        # This deliberately returns a model write proposal which is invalid
        # for the strict requested calculation; it must not reach an executor.
        return _primary(message, kind="respond", effect="none")
    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("selected read cannot become write")))
    cfg = _config(tmp_path, profile)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "explicit-no-write-001",
            "selected_ordinary_operation": "calculate",
            "memory_write_consent": True})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "explicit_ordinary_primary_mismatch"
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []


@pytest.mark.parametrize("message", [
    "请计算 10**100000", "请计算 (19+23)*4，并保存计划",
    "请创建任务：提交周报",
])
def test_malformed_selected_request_fails_before_primary(
        tmp_path, monkeypatch, message):
    async def forbidden(*args, **kwargs):
        raise AssertionError("rejected selection must not call primary")
    monkeypatch.setattr("apps.api.main.propose_primary_route", forbidden)
    cfg = _config(tmp_path, "unified")
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message, "client_request_id": "explicit-invalid-001",
            "selected_ordinary_operation": "calculate"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "explicit_ordinary_admission_denied"
