"""M4: opt-in guarded cancellation never grants tool or database authority."""
import json

from fastapi.testclient import TestClient
from agent.config import Settings
from agent.m4_guarded_cancel import admit_guarded_plan_cancel
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


def test_m4_admission_negative_and_positive():
    checkpoint = {"revision": 1, "steps": [
        {"id": 1, "status": "pending", "goal": "计算 2+2",
         "expected_tool": "calculate", "expected_arguments": {"expression": "2+2"}}]}
    args = dict(message="取消刚才保存的计划，不要执行其中的步骤。",
                conversation_id="cid", request_id="request-001",
                checkpoint=checkpoint, turn_created=True,
                semantic_confirmed=True)
    assert admit_guarded_plan_cancel(**args)
    assert not admit_guarded_plan_cancel(**(args | {"semantic_confirmed": False}))
    assert not admit_guarded_plan_cancel(**(args | {"turn_created": False}))
    assert not admit_guarded_plan_cancel(**(args | {"checkpoint": None}))
    assert not admit_guarded_plan_cancel(**(args | {"message": "如何取消计划？"}))
    assert not admit_guarded_plan_cancel(**(args | {"message": "执行当前保存的计划"}))
    assert not admit_guarded_plan_cancel(**(args | {"checkpoint": {
        "revision": 0, "steps": checkpoint["steps"]}}))


def test_m4_default_legacy_and_explicit_guarded_setting():
    assert Settings().route_admission_mode == "legacy"
    assert Settings(route_admission_mode="guarded").route_admission_mode == "guarded"


def test_m4_guarded_cancel_live_api_contract_without_tools(tmp_path, monkeypatch):
    from agent.loop import AgentLoop

    understood = json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "保存计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}
    }, ensure_ascii=False)

    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood

    async def fake_plan_intent(provider, model, message, existing_plan):
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "计算 12*13", "expected_tool": "calculate",
             "expected_arguments": {"expression": "12*13"}},
            {"goal": "计算 7*9", "expected_tool": "calculate",
             "expected_arguments": {"expression": "7*9"}},
        ])

    async def confirm_create(*args, **kwargs):
        return True

    async def confirm_cancel(provider, model, message):
        return message.startswith("取消刚才")

    async def forbidden_run(self, *args, **kwargs):
        raise AssertionError("cancel must not invoke AgentLoop")

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_intent)
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", confirm_create)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", confirm_cancel)
    monkeypatch.setattr(AgentLoop, "run", forbidden_run)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm4_guarded.db'}",
        model_provider="ollama_proxy", understanding_strategy="structured",
        route_admission_mode="guarded")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        created = client.post(endpoint, json={
            "content": "请创建一个两步计划：第一步计算 12*13，第二步计算 7*9。只保存计划，不执行。",
            "client_request_id": "m4-guarded-create-001"})
        assert created.status_code == 200, created.text
        assert created.json()["termination_reason"] == "plan_created_pending_execution"
        cancelled = client.post(endpoint, json={
            "content": "取消刚才保存的计划，不要执行其中的步骤。",
            "client_request_id": "m4-guarded-cancel-002"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["termination_reason"] == "plan_cancelled"
        assert cancelled.json()["trace"] == [{"step": 0, "type": "plan_cancelled"}]
        assert cancelled.json()["plan"] == []


def test_m4_guarded_plan_create_requires_unique_current_turn_bindings():
    from agent.m4_guarded_plan import admit_guarded_plan_create
    checkpoint = {"revision": 1, "steps": [
        {"id": 1, "status": "pending", "goal": "计算 3*4",
         "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
        {"id": 2, "status": "pending", "goal": "计算 5*6",
         "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}}]}
    args = dict(message="请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。",
                conversation_id="cid", request_id="req-00001",
                checkpoint=checkpoint, turn_created=True, semantic_confirmed=True)
    assert admit_guarded_plan_create(**args)
    assert not admit_guarded_plan_create(**(args | {"turn_created": False}))
    assert not admit_guarded_plan_create(**(args | {"semantic_confirmed": False}))
    assert not admit_guarded_plan_create(**(args | {"message": "创建计划 3*4"}))
    assert not admit_guarded_plan_create(**(args | {
        "message": "创建计划：第一步算 3*4；第二步也算 3*4；第三步算 5*6"}))
    forged = {"revision": 1, "steps": [
        checkpoint["steps"][0],
        {**checkpoint["steps"][1], "expected_arguments": {"expression": "9*9"}}]}
    assert not admit_guarded_plan_create(**(args | {"checkpoint": forged}))


def test_m4_invalid_confirmed_plan_never_enters_agent_loop(tmp_path, monkeypatch):
    from agent.loop import AgentLoop

    understood = json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "保存计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}
    }, ensure_ascii=False)

    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood

    async def invalid_plan(*args, **kwargs):
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "计算不在请求内的表达式", "expected_tool": "calculate",
             "expected_arguments": {"expression": "999*888"}}])

    async def confirmed(*args, **kwargs):
        return True

    async def deny_tools(self, *args, **kwargs):
        raise AssertionError("unbound_plan_must_not_run_tools")

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.review_plan_intent", invalid_plan)
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", confirmed)
    monkeypatch.setattr(AgentLoop, "run", deny_tools)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm4_invalid.db'}",
        model_provider="ollama_proxy", understanding_strategy="structured",
        route_admission_mode="guarded")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "创建一个包含两步的计划，暂时不要执行",
            "client_request_id": "m4-guarded-invalid-001"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["termination_reason"] == "plan_creation_requires_clarification"
        assert data["trace"] == [{"step": 0, "type": "plan_execution_fenced"}]
        assert not data.get("plan_checkpoint")


def test_m4_guarded_plan_execution_scopes_and_validation():
    from agent.m4_guarded_execution import admit_guarded_plan_execution
    steps = [
        {"id": 1, "status": "pending", "goal": "计算 3*4",
         "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
        {"id": 2, "status": "pending", "goal": "计算 5*6",
         "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}}]
    snapshot = {"revision": 2, "steps": steps}
    args = dict(conversation_id="cid", request_id="request-001",
                checkpoint=snapshot, scope="execute_first",
                turn_created=True, semantic_confirmed=True)
    assert admit_guarded_plan_execution(**args)
    assert admit_guarded_plan_execution(**(args | {"scope": "resume"}))
    assert not admit_guarded_plan_execution(**(args | {"semantic_confirmed": False}))
    assert not admit_guarded_plan_execution(**(args | {"turn_created": False}))
    assert not admit_guarded_plan_execution(**(args | {"checkpoint": None}))
    assert not admit_guarded_plan_execution(**(args | {"scope": "all"}))
    completed_first = {"revision": 2, "steps": [{**steps[0], "status": "completed"}, steps[1]]}
    assert not admit_guarded_plan_execution(**(args | {"checkpoint": completed_first}))
    assert admit_guarded_plan_execution(**(args | {
        "checkpoint": completed_first, "scope": "resume"}))
    invalid_binding = {"revision": 2, "steps": [
        {**steps[0], "expected_arguments": {"expression": 123}}, steps[1]]}
    assert not admit_guarded_plan_execution(**(args | {"checkpoint": invalid_binding}))


def test_m4_saved_plan_command_is_not_swallowed_by_generic_goal_clarification(
        tmp_path, monkeypatch):
    from agent.understanding import UnderstandingService, UnderstandingResult
    from agent.loop import AgentLoop
    from agent.execution_policy import ExecutionPolicyDecision

    async def fake_understand(self, context):
        is_execute = "只执行已保存" in context.current_message
        data = {
            "schema_version": "s25-01-v1",
            "status": "clarification_required" if is_execute else "understood",
            "goal": None if is_execute else {
                "objective": "创建待执行计划", "subgoals": [], "constraints": [],
                "success_criteria": []},
            "change": {"kind": "continue" if is_execute else "new_goal",
                       "target_goal_id": None, "delta": None},
            "clarification": {
                "required": is_execute,
                "reason_code": "ambiguous_target" if is_execute else "none",
                "question": "如何执行第一步？" if is_execute else None},
        }
        return {"result": UnderstandingResult.model_validate(data), "attempts": []}

    async def intent(provider, model, message, existing_plan):
        if existing_plan:
            return PlanRequest(intent="execute_first")
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "计算 3*4", "expected_tool": "calculate",
             "expected_arguments": {"expression": "3*4"}},
            {"goal": "计算 5*6", "expected_tool": "calculate",
             "expected_arguments": {"expression": "5*6"}},
        ])

    async def allow(*args, **kwargs):
        return True

    async def policy(*args, **kwargs):
        return ExecutionPolicyDecision(mode="read_only", target="tool_action")

    called = []
    async def fake_run(self, *args, **kwargs):
        called.append(kwargs.get("plan_control"))
        assert kwargs.get("plan_control") == "execute_first"
        checkpoint = kwargs["plan_checkpoint"]
        assert [x["status"] for x in checkpoint["steps"]] == ["pending", "pending"]
        updated = {**checkpoint, "steps": [
            {**checkpoint["steps"][0], "status": "completed",
             "evidence_tool": "calculate"},
            checkpoint["steps"][1]]}
        return {"status": "partial", "answer": "first step verified",
                "trace": [{"step": 1, "type": "tool", "name": "calculate", "ok": True}],
                "steps": 1, "plan": updated["steps"],
                "plan_revision": updated["revision"],
                "plan_checkpoint": updated}

    monkeypatch.setattr(UnderstandingService, "understand", fake_understand)
    monkeypatch.setattr("apps.api.main.review_plan_intent", intent)
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", allow)
    monkeypatch.setattr("apps.api.main.review_plan_execution_authorization", allow)
    monkeypatch.setattr("apps.api.main.decide_execution_policy_detail", policy)
    monkeypatch.setattr(AgentLoop, "run", fake_run)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm4_priority.db'}",
        model_provider="ollama_proxy", understanding_strategy="structured",
        route_admission_mode="guarded")
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        created = client.post(endpoint, json={
            "content": "请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。",
            "client_request_id": "m4-priority-001"})
        assert created.status_code == 200, created.text
        assert created.json()["termination_reason"] == "plan_created_pending_execution"
        first = client.post(endpoint, json={
            "content": "现在只执行已保存计划的第一步，第二步先不要执行。",
            "client_request_id": "m4-priority-002"})
        assert first.status_code == 200, first.text
        assert first.json()["trace"][0]["type"] == "tool"
        assert called == ["execute_first"]


def test_m4_model_endpoint_reports_runtime_route_mode(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm4_mode.db'}",
        route_admission_mode="guarded")
    with TestClient(create_app(settings)) as client:
        response = client.get("/model")
        assert response.status_code == 200
        assert response.json()["route_admission_mode"] == "guarded"
        assert response.json()["m4_contract"] == "guarded-plan-route-priority-v2"
