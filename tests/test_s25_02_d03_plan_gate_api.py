
"""D03-F01: plan authoring cannot execute; explicit step commands resume saved evidence."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.plan_intent import PlanRequest
from apps.api.main import create_app
import pytest


def understood():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "按步骤处理只读计算", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}
    }, ensure_ascii=False)


def test_d03_create_first_resume_no_premature_tool_calls(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    calls = []
    async def fake_plan_review(provider, model, message, existing_plan):
        calls.append((message, existing_plan))
        if len(calls) == 1:
            return PlanRequest(intent="plan_only", steps=[
                {"goal": "计算 2+2", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "2+2"}},
                {"goal": "计算 3+3", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "3+3"}},
            ])
        if len(calls) == 2:
            return PlanRequest(intent="execute_first")
        return PlanRequest(intent="resume")
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_review)
    async def policy(message, understanding):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d03.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        def send(message, suffix):
            res=client.post(route,json={"content":message, "client_request_id":suffix})
            assert res.status_code == 200, res.text
            return res.json()
        first=send("建立计划：第一步算2+2，第二步算3+3", "plan-case-0001")
        assert first["termination_reason"] == "plan_created_pending_execution"
        assert first["plan_revision"] == 1
        assert [s["status"] for s in first["plan"]] == ["pending","pending"]
        assert not any(x["type"] == "tool" for x in first["trace"])
        second=send("只执行第一步，其余保留", "plan-case-0002")
        assert second["status"] == "partial"
        assert [s["status"] for s in second["plan"]] == ["completed","pending"]
        third=send("继续执行剩余步骤", "plan-case-0003")
        assert third["status"] == "completed"
        assert [s["status"] for s in third["plan"]] == ["completed","completed"]
        assert len([x for x in third["trace"] if x["type"] == "tool"]) == 1
        assert calls == [(calls[0][0],False),(calls[1][0],True),(calls[2][0],True)]


def test_d03_rejects_unsupported_and_ungrounded_plan_binding():
    from agent.plan_intent import build_plan_checkpoint
    import pytest
    with pytest.raises(ValueError):
        build_plan_checkpoint("建立计划", PlanRequest(intent="plan_only",steps=[
            {"goal":"调用Shell","expected_tool":"shell",
             "expected_arguments":{"command":"echo hi"}}]))
    with pytest.raises(ValueError):
        build_plan_checkpoint("建立计划计算 1+1", PlanRequest(intent="plan_only",steps=[
            {"goal":"计算 100+100","expected_tool":"calculate",
             "expected_arguments":{"expression":"100+100"}}]))


def test_d03_f02_misrouted_respond_only_can_resume_verified_pending_plan(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    intents = iter([
        PlanRequest(intent="plan_only", steps=[
            {"goal": "计算 2+2", "expected_tool": "calculate",
             "expected_arguments": {"expression": "2+2"}},
            {"goal": "计算 3+3", "expected_tool": "calculate",
             "expected_arguments": {"expression": "3+3"}}]),
        PlanRequest(intent="execute_first"),
        PlanRequest(intent="resume"),
        PlanRequest(intent="resume"),
    ])
    async def fake_plan_intent(provider, model, message, existing_plan):
        return next(intents)
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_intent)
    reviews = []
    async def fake_resume_review(provider, model, message, checkpoint, intent):
        reviews.append((intent, len([s for s in checkpoint["steps"] if s["status"] == "pending"])))
        return True
    monkeypatch.setattr("apps.api.main.review_plan_execution_authorization", fake_resume_review)
    async def policy(message, interpretation):
        if "继续" in message:
            return "respond_only"  # Reproduce real Qwen's failed D03 route.
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d03f02.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        def send(msg, rid):
            response = client.post(route, json={"content": msg, "client_request_id": rid})
            assert response.status_code == 200, response.text
            return response.json()
        first = send("建立计划计算 2+2 和 3+3", "d03f02-001")
        assert first["termination_reason"] == "plan_created_pending_execution"
        second = send("只完成第一步", "d03f02-002")
        assert [s["status"] for s in second["plan"]] == ["completed", "pending"]
        third = send("继续完成计划中剩余步骤，不重复第一步", "d03f02-003")
        assert third["status"] == "completed"
        assert third["execution_mode"] == "read_only"
        assert [s["status"] for s in third["plan"]] == ["completed", "completed"]
        assert len([x for x in third["trace"] if x["type"] == "tool"]) == 1
        assert reviews == [("resume", 1)]
        fourth = send("请继续完成计划中剩余步骤，不要重复第一步", "d03f02-004")
        assert fourth["status"] == "completed"
        assert fourth["termination_reason"] == "plan_already_completed"
        assert fourth["trace"] == [{"step": 0, "type": "plan_already_completed"}]
        assert fourth["steps"] == 0
        assert fourth["plan"] == third["plan"]
        assert fourth["plan_checkpoint"] == third["plan_checkpoint"]
        assert fourth["plan_revision"] == third["plan_revision"]
        assert reviews == [("resume", 1)]  # No fourth-round execution re-review.



def test_d03_f02_explicit_denial_remains_fail_closed(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    intents = iter([
        PlanRequest(intent="plan_only", steps=[{
            "goal": "计算 2+2", "expected_tool": "calculate",
            "expected_arguments": {"expression": "2+2"}}]),
        PlanRequest(intent="resume"),
    ])
    async def fake_plan_intent(provider, model, message, existing_plan):
        return next(intents)
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_intent)
    async def deny(*args):
        return False
    monkeypatch.setattr("apps.api.main.review_plan_execution_authorization", deny)
    async def policy(message, interpretation):
        return "respond_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'deny.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        def send(msg, rid):
            response = client.post(route, json={"content": msg, "client_request_id": rid})
            assert response.status_code == 200, response.text
            return response.json()
        first = send("建立计划计算 2+2", "d03deny-001")
        assert first["plan"][0]["status"] == "pending"
        second = send("继续执行剩余步骤", "d03deny-002")
        assert second["termination_reason"] == "execution_not_authorized"
        assert second["trace"][0]["type"] == "plan_execution_denied"
        assert second["steps"] == 0

# These API regression fixtures explicitly supply a formal saved-plan request.
# Stub only the new semantic admission review; the actual production model
# contract is tested separately in test_s25_02_f03_plan_admission.py.
@pytest.fixture(autouse=True)
def admit_fixture_saved_plan(monkeypatch):
    async def admitted(*args, **kwargs):
        return True
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", admitted)


def test_explicit_save_only_plan_never_falls_back_to_tools(tmp_path, monkeypatch):
    from agent.loop import AgentLoop
    from agent.plan_control import explicit_deferred_plan_request

    message = "请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。"
    assert explicit_deferred_plan_request(message)

    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood()

    async def missing_plan_proposal(*args, **kwargs):
        return PlanRequest(intent="other", steps=[])

    async def should_never_run(self, *args, **kwargs):
        raise AssertionError("unsafe_agent_loop_fallback")

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.review_plan_intent", missing_plan_proposal)
    monkeypatch.setattr(AgentLoop, "run", should_never_run)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'no_fallback.db'}",
        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        res = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message, "client_request_id": "save-only-no-run-001"})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["termination_reason"] == "plan_creation_requires_clarification"
        assert data["trace"] == [{"step": 0, "type": "plan_execution_fenced"}]
        assert not data.get("plan")
        assert data.get("plan_checkpoint") is None
        assert not any(t["type"] == "tool" for t in data["trace"])


def test_deferred_plan_fence_does_not_block_independent_calculation():
    from agent.plan_control import explicit_deferred_plan_request
    assert not explicit_deferred_plan_request("请使用计算工具计算 3*4")
    assert not explicit_deferred_plan_request(
        "旧计划不要执行，现在请计算 3*4")


def test_m3_cancel_saved_plan_is_host_owned_and_durable(tmp_path, monkeypatch):
    from agent.loop import AgentLoop
    from services.plan_store import ConversationPlanStore
    from services.db import database

    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood()

    async def fake_plan_intent(provider, model, message, existing_plan):
        if not existing_plan:
            return PlanRequest(intent="plan_only", steps=[
                {"goal": "计算 12*13", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "12*13"}},
                {"goal": "计算 7*9", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "7*9"}}])
        return PlanRequest(intent="other")

    async def confirm_create(*args, **kwargs):
        return True

    async def confirm_cancel(provider, model, message):
        return "取消" in message and "计划" in message

    async def should_never_run(self, *args, **kwargs):
        raise AssertionError("cancel_must_not_enter_agent_loop")

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_intent)
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", confirm_create)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", confirm_cancel)
    monkeypatch.setattr(AgentLoop, "run", should_never_run)

    dburl = f"sqlite:///{tmp_path / 'cancel.db'}"
    settings = Settings(database_url=dburl, model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        url = f"/conversations/{cid}/agent/messages"
        first = client.post(url, json={
            "content": "请创建一个两步计划：第一步计算 12*13，第二步计算 7*9。只保存计划，不执行。",
            "client_request_id": "cancel-host-create-001"})
        assert first.status_code == 200, first.text
        assert first.json()["termination_reason"] == "plan_created_pending_execution"
        cancelled = client.post(url, json={
            "content": "取消刚才保存的计划，不要执行其中的步骤。",
            "client_request_id": "cancel-host-cancel-002"})
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()
        assert body["termination_reason"] == "plan_cancelled"
        assert body["status"] == "completed"
        assert body["trace"] == [{"step": 0, "type": "plan_cancelled"}]
        assert not body["plan"]
        engine, session = database(dburl)
        try:
            assert ConversationPlanStore(session, cid).load() is None
        finally:
            engine.dispose()
