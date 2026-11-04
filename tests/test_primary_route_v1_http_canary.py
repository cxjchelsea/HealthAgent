"""Opt-in v1 HTTP canary: one semantic owner and bounded Host operations.

These tests intentionally protect the legacy Unified default and prove that
unwired v1 intents are denied rather than silently reclassified or dispatched.
"""
import json

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import ConversationPlan, database


CREATE = "请创建一个两步可执行计划：第一步计算 3+4，第二步计算 8+9。只保存，不执行。"
CANCEL = "请取消当前保存的计划，不执行任何步骤。"


def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'v1_canary.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True)


def bound_create():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "plan_create", "quote": "创建一个两步可执行计划",
        "execution_scope": "none", "effect_intent": "mutation_requested",
        "steps": [
            {"goal": "计算 3+4", "tool_name": "calculate",
             "arguments": {"expression": "3+4"}, "quote": "计算 3+4"},
            {"goal": "计算 8+9", "tool_name": "calculate",
             "arguments": {"expression": "8+9"}, "quote": "计算 8+9"},
        ],
    }), CREATE, saved_plan_present=False, pending_action=None)


def bound_cancel():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "plan_cancel", "quote": "取消当前保存的计划",
        "execution_scope": "none", "effect_intent": "mutation_requested",
    }), CANCEL, saved_plan_present=True, pending_action=None)


def test_canary_create_cancel_uses_single_primary_and_host_cas(tmp_path, monkeypatch):
    calls = []

    async def route(*args, **kwargs):
        calls.append(kwargs["saved_plan_present"])
        return bound_cancel() if kwargs["saved_plan_present"] else bound_create()

    async def legacy_forbidden(*args, **kwargs):
        raise AssertionError("must never reach legacy semantic route")

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", legacy_forbidden)

    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        result = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": CREATE, "client_request_id": "v1-create-001"})
        assert result.status_code == 200, result.text
        assert result.json()["termination_reason"] == "plan_created_pending_execution"
        assert [s["status"] for s in result.json()["plan"]] == ["pending", "pending"]
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.get(ConversationPlan, cid) is not None
        cancelled = client.post(f"/conversations/{cid}/agent/messages",
                                json={"content": CANCEL, "client_request_id": "v1-cancel-001"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["termination_reason"] == "plan_cancelled"
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None
    assert calls == [False, True]


def test_canary_ordinary_read_only_preserves_host_mode(tmp_path, monkeypatch):
    calls = []

    async def ordinary(*args, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
            "quote": "请计算", "execution_scope": "none",
            "effect_intent": "read_only",
        }), "请计算 5+2", saved_plan_present=False, pending_action=None)

    async def no_legacy(*args, **kwargs):
        raise AssertionError("ordinary v1 must not reclassify route")

    class Loop:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        async def run(self, *args, **kwargs):
            assert kwargs["execution_mode"] == "read_only"
            assert kwargs["plan_control"] is None
            assert kwargs["memory_write_consent"] is False
            assert kwargs["understanding_result"] is None
            return {"run_id": "fake", "status": "completed", "answer": "7",
                    "termination_reason": "model_final",
                    "trace": [{"step": 0, "type": "final"}], "steps": 0}

    monkeypatch.setattr("apps.api.main.propose_primary_route", ordinary)
    monkeypatch.setattr("apps.api.main.AgentLoop", Loop)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", no_legacy)
    monkeypatch.setattr("apps.api.main.decide_execution_policy_detail", no_legacy)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        result = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": "请计算 5+2", "client_request_id": "v1-ordinary-001"})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["ordinary_attempt_journal"] is not None


def test_canary_create_task_atomic_write_and_replay_denial(tmp_path, monkeypatch):
    from services.db import AgentAttempt, ConversationTask
    from sqlalchemy import select
    message = "创建任务：提交测试报告"

    async def task_route(*args, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "ordinary_tool", "quote": message,
            "execution_scope": "none",
            "effect_intent": "mutation_requested",
            "write_tool_name": "create_task",
            "write_arguments": {"title": "提交测试报告"},
        }), message, saved_plan_present=False, pending_action=None)

    def no_agent(*args, **kwargs):
        raise AssertionError("write must not enter unbounded AgentLoop")

    monkeypatch.setattr("apps.api.main.propose_primary_route", task_route)
    monkeypatch.setattr("apps.api.main.AgentLoop", no_agent)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        request = {"content": message, "client_request_id": "v1-task-write-001"}
        result = client.post(f"/conversations/{cid}/agent/messages", json=request)
        assert result.status_code == 200, result.text
        assert result.json()["termination_reason"] == "primary_v1_task_created"
        assert result.json()["status"] == "completed"
        replay = client.post(f"/conversations/{cid}/agent/messages", json=request)
        assert replay.status_code == 409
        _, Session = database(cfg.database_url)
        with Session() as db:
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            attempts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            assert len(tasks) == len(attempts) == 1
            assert tasks[0].title == "提交测试报告"
            assert attempts[0].status == "succeeded"
            receipt = json.loads(attempts[0].result_projection)
            assert receipt["task_id"] == tasks[0].id


def test_canary_unbound_write_is_not_executable(tmp_path, monkeypatch):
    from agent.primary_route_v1 import PrimaryRouteRejected

    async def invalid_write(*args, **kwargs):
        raise PrimaryRouteRejected("primary_route_write_arguments_unbound")

    def forbidden(*args, **kwargs):
        raise AssertionError("unbound write must not run tools")

    monkeypatch.setattr("apps.api.main.propose_primary_route", invalid_write)
    monkeypatch.setattr("apps.api.main.AgentLoop", forbidden)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "创建任务", "client_request_id": "v1-unbound-write-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "primary_v1_proposal_refused"


def test_canary_invalid_primary_cannot_fall_back(tmp_path, monkeypatch):
    from agent.primary_route_v1 import PrimaryRouteRejected

    async def invalid(*args, **kwargs):
        raise PrimaryRouteRejected("primary_route_quote_ambiguous")

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy after denial")

    monkeypatch.setattr("apps.api.main.propose_primary_route", invalid)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", forbidden)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages",
                           json={"content": CREATE, "client_request_id": "v1-invalid-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "primary_v1_proposal_refused"
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None


def test_canary_first_resume_through_m4_and_durable_plan(tmp_path, monkeypatch):
    """Create -> FIRST -> RESUME; every request uses one v1 proposal only."""
    calls = []

    async def route(*args, **kwargs):
        present = kwargs["saved_plan_present"]
        content = args[2]
        calls.append((content, present))
        if not present:
            return bound_create()
        if "第一步" in content:
            kind, scope, quote = "plan_execute_first", "first_step", "第一步"
        else:
            kind, scope, quote = "plan_resume", "remaining_steps", "剩余步骤"
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": kind, "quote": quote,
            "execution_scope": scope, "effect_intent": "read_only",
        }), content, saved_plan_present=True, pending_action=None)

    async def no_legacy(*args, **kwargs):
        raise AssertionError("v1 execution cannot call old semantic classifier")

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    for name in ("propose_new_plan_route", "propose_saved_plan_route",
                 "review_plan_intent", "review_plan_execution_authorization",
                 "review_plan_execution_scope", "reconcile_execution_policy"):
        monkeypatch.setattr("apps.api.main." + name, no_legacy)

    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        def send(text, request_id):
            resp = client.post(f"/conversations/{cid}/agent/messages", json={
                "content": text, "client_request_id": request_id,
            })
            assert resp.status_code == 200, resp.text
            return resp.json()

        create = send(CREATE, "v1-execute-create-001")
        assert create["termination_reason"] == "plan_created_pending_execution"
        first = send("只执行已保存计划的第一步", "v1-execute-first-001")
        assert first["termination_reason"] == "pending_plan_steps"
        assert [s["status"] for s in first["plan"]] == ["completed", "pending"]
        assert [t["name"] for t in first["trace"] if t["type"] == "tool"] == ["calculate"]
        resume = send("继续执行剩余步骤", "v1-execute-resume-001")
        assert resume["termination_reason"] == "plan_execution_completed"
        assert [s["status"] for s in resume["plan"]] == ["completed", "completed"]
        assert [t["name"] for t in resume["trace"] if t["type"] == "tool"] == ["calculate"]
        assert len(calls) == 3
        _, Session = database(cfg.database_url)
        with Session() as db:
            saved = db.get(ConversationPlan, cid)
            assert saved is not None
            assert [s["status"] for s in json.loads(saved.snapshot)["steps"]] == [
                "completed", "completed"]


def test_canary_first_denies_missing_checkpoint_without_tools(tmp_path, monkeypatch):
    async def forged(*args, **kwargs):
        from agent.primary_route_v1 import PrimaryRouteRejected
        raise PrimaryRouteRejected("primary_route_missing_saved_plan")

    monkeypatch.setattr("apps.api.main.propose_primary_route", forged)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "只执行第一步", "client_request_id": "v1-missing-first-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "primary_v1_proposal_refused"


def test_canary_revise_uses_primary_binding_and_cas(tmp_path, monkeypatch):
    """CREATE -> REVISE updates only step two without legacy revision model."""
    revision_request = "把已保存计划的第二步由8+9改成10+11，第一步不变。"
    calls = []

    async def route(*args, **kwargs):
        calls.append(args[2])
        if not kwargs["saved_plan_present"]:
            return bound_create()
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "plan_revise", "quote": "第二步由8+9改成10+11",
            "execution_scope": "none", "effect_intent": "mutation_requested",
            "step_number": 2, "revision_old_argument": "8+9",
            "revision_new_argument": "10+11",
        }), revision_request, saved_plan_present=True,
            pending_action=None, saved_step_count=2)

    async def forbidden(*args, **kwargs):
        raise AssertionError("second revision classifier must not run")

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    monkeypatch.setattr("apps.api.main.review_plan_revision", forbidden)
    monkeypatch.setattr("apps.api.main.check_plan_edit_intent", forbidden)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", forbidden)

    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        created = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": CREATE, "client_request_id": "v1-revise-create-001"})
        assert created.status_code == 200, created.text
        assert created.json()["termination_reason"] == "plan_created_pending_execution"
        before = created.json()["plan"]
        revised = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": revision_request, "client_request_id": "v1-revise-001"})
        assert revised.status_code == 200, revised.text
        data = revised.json()
        assert data["termination_reason"] == "plan_revised_pending_execution"
        assert data["plan"][0] == before[0]
        assert data["plan"][1]["expected_arguments"] == {"expression": "10+11"}
        assert data["plan_revision"] == 2
        assert not any(t["type"] == "tool" for t in data["trace"])
        _, Session = database(cfg.database_url)
        with Session() as db:
            saved = db.get(ConversationPlan, cid)
            assert json.loads(saved.snapshot)["revision"] == 2
    assert len(calls) == 2


def test_canary_revise_conflicting_old_argument_is_not_written(tmp_path, monkeypatch):
    request = "把已保存计划的第二步由9+9改成10+11。"

    async def route(*args, **kwargs):
        if not kwargs["saved_plan_present"]:
            return bound_create()
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "plan_revise", "quote": "第二步由9+9改成10+11",
            "execution_scope": "none", "effect_intent": "mutation_requested",
            "step_number": 2, "revision_old_argument": "9+9",
            "revision_new_argument": "10+11",
        }), request, saved_plan_present=True, pending_action=None,
            saved_step_count=2)

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        first = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": CREATE, "client_request_id": "v1-revise-negative-create-001"})
        assert first.json()["termination_reason"] == "plan_created_pending_execution"
        _, Session = database(cfg.database_url)
        with Session() as db:
            original = db.get(ConversationPlan, cid).snapshot
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": request, "client_request_id": "v1-revise-negative-001"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "primary_v1_revision_old_argument_mismatch"
        with Session() as db:
            assert db.get(ConversationPlan, cid).snapshot == original


def test_canary_pending_read_only_is_journaled_without_legacy(tmp_path, monkeypatch):
    """Trusted pending MCP continuation prepares and finishes durable evidence."""
    from services.db import AgentAttempt, Message, Turn
    from sqlalchemy import select

    async def pending_primary(*args, **kwargs):
        assert kwargs["pending_action"] == "mcp_reference_lookup"
        assert kwargs["pending_origin_verified"] is True
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "clarify_response", "quote": "MCP",
        }), "MCP", saved_plan_present=False,
            pending_action="mcp_reference_lookup", pending_origin_verified=True)

    async def forbidden(*args, **kwargs):
        raise AssertionError("pending cannot call legacy semantics")

    def fake_tool(name, arguments, tasks, **kwargs):
        assert name == "mcp_reference_lookup"
        assert arguments == {"term": "MCP"}
        return {"ok": True, "definition": "Model Context Protocol", "source": "test"}

    monkeypatch.setattr("apps.api.main.propose_primary_route", pending_primary)
    monkeypatch.setattr("apps.api.main.classify_pending_reply", forbidden)
    monkeypatch.setattr("apps.api.main.execute_tool",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("unfenced legacy tool execution")))
    monkeypatch.setattr("agent.primary_pending_readonly_v1.execute_tool", fake_tool)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        with Session.begin() as db:
            assistant = Message(conversation_id=cid, role="assistant",
                                content="请提供词条", status="partial")
            db.add(assistant)
            db.flush()
            db.add(Turn(conversation_id=cid,
                        request_id="v1-pending-prior-001",
                        state="partial", assistant_message_id=assistant.id,
                        failure_code="needs_input:mcp_reference_lookup"))
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "MCP", "client_request_id": "v1-pending-response-001"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "verified_clarification_execution"
        assert reply.json()["status"] == "completed"
        with Session() as db:
            attempts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            assert len(attempts) == 1
            assert attempts[0].tool_name == "mcp_reference_lookup"
            assert attempts[0].status == "succeeded"
            assert attempts[0].result_projection is not None
        # A retry with the SAME request_id cannot dispatch the tool twice.
        replay = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "MCP", "client_request_id": "v1-pending-response-001"})
        assert replay.status_code == 409
        with Session() as db:
            assert len(db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()) == 1


def test_canary_pending_write_remains_restricted(tmp_path, monkeypatch):
    from services.db import Message, Turn

    async def pending_primary(*args, **kwargs):
        assert kwargs["pending_action"] == "create_task"
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "clarify_response", "quote": "提交报告",
        }), "提交报告", saved_plan_present=False,
            pending_action="create_task", pending_origin_verified=True)

    monkeypatch.setattr("apps.api.main.propose_primary_route", pending_primary)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        with Session.begin() as db:
            assistant = Message(conversation_id=cid, role="assistant",
                                content="请提供任务标题", status="partial")
            db.add(assistant)
            db.flush()
            db.add(Turn(conversation_id=cid,
                        request_id="v1-pending-write-prior-001",
                        state="partial", assistant_message_id=assistant.id,
                        failure_code="needs_input:create_task"))
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "提交报告", "client_request_id": "v1-pending-write-001"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "primary_v1_pending_origin_unverified"
        with Session() as db:
            from services.db import ConversationTask
            from sqlalchemy import select
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []


def test_canary_pending_origin_missing_fails_closed(tmp_path, monkeypatch):
    from agent.primary_route_v1 import PrimaryRouteRejected

    async def reject(*args, **kwargs):
        raise PrimaryRouteRejected("primary_route_untrusted_pending_origin")

    monkeypatch.setattr("apps.api.main.propose_primary_route", reject)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "MCP", "client_request_id": "v1-pending-untrusted-001"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "primary_v1_proposal_refused"


def test_primary_revise_preserves_explicit_do_not_touch_step(tmp_path, monkeypatch):
    """Known regression after first unseen-v1: a preserve clause is not another edit."""
    request = "把已保存计划的第二步由8+9改成10+11，第一步不要动。"

    async def primary(*args, **kwargs):
        if not kwargs["saved_plan_present"]:
            return bound_create()
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "plan_revise",
            "quote": "第二步由8+9改成10+11",
            "execution_scope": "none",
            "effect_intent": "mutation_requested",
            "step_number": 2,
            "revision_old_argument": "8+9",
            "revision_new_argument": "10+11",
        }), request, saved_plan_present=True, pending_action=None,
            saved_step_count=2)

    async def no_legacy(*args, **kwargs):
        raise AssertionError("no second semantic review permitted")

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_revision", no_legacy)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        created = client.post(endpoint, json={
            "content": CREATE, "client_request_id": "preserve-create-001"})
        assert created.status_code == 200, created.text
        assert created.json()["termination_reason"] == "plan_created_pending_execution"
        original = created.json()["plan"]
        revised = client.post(endpoint, json={
            "content": request, "client_request_id": "preserve-revise-001"})
        assert revised.status_code == 200, revised.text
        body = revised.json()
        assert body["termination_reason"] == "plan_revised_pending_execution"
        assert body["plan"][0] == original[0]
        assert body["plan"][1]["expected_arguments"] == {"expression": "10+11"}
        assert all(step["status"] == "pending" for step in body["plan"])
        assert not any(e.get("type") == "tool" for e in body.get("trace", []))
        _, Session = database(cfg.database_url)
        with Session() as db:
            persisted = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert persisted["steps"][0] == original[0]
            assert persisted["steps"][1]["expected_arguments"] == {"expression": "10+11"}
