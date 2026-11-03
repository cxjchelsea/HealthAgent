"""AC2: all thirteen retired reviewers must be unreachable in primary Unified.

Runs the real HTTP endpoint with real-provider auto-routing configuration;
only the semantic model output is replaced with deterministic proposals.
This is not a Qwen live-model acceptance test.
"""
import pytest
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import database, Message, Turn

LEGACY_REVIEWERS = (
    "review_plan_cancellation",
    "propose_first_route",
    "review_plan_intent",
    "check_plan_edit_intent",
    "classify_pending_reply",
    "review_pending_reference_lookup",
    "review_unsupported_capability",
    "review_read_only_clarification",
    "reconcile_execution_policy",
    "review_plan_creation_reference",
    "review_missing_plan_reference",
    "review_plan_execution_authorization",
    "review_plan_execution_scope",
)

CREATE = "请创建两个步骤：先计算3+4，然后计算8+9；只保存，不执行。"
CANCEL = "取消当前保存的计划"
WRITE = "请创建任务：提交周报"
RESPOND = "请简单介绍这个项目"
REJECT = "请执行不存在的工具"
PENDING = "MCP"


def _proposal(kind, message, *, saved=False, pending=None):
    if kind == "plan_create":
        data = {
            "route_kind": kind, "quote": "创建两个步骤",
            "effect_intent": "mutation_requested",
            "steps": [
                {"goal": "计算3+4", "tool_name": "calculate",
                 "arguments": {"expression": "3+4"}, "quote": "计算3+4"},
                {"goal": "计算8+9", "tool_name": "calculate",
                 "arguments": {"expression": "8+9"}, "quote": "计算8+9"},
            ],
        }
    elif kind == "plan_cancel":
        data = {"route_kind": kind, "quote": "取消当前保存的计划",
                "effect_intent": "mutation_requested"}
    elif kind == "ordinary_tool":
        data = {"route_kind": kind, "quote": WRITE,
                "effect_intent": "mutation_requested",
                "write_tool_name": "create_task",
                "write_arguments": {"title": "提交周报"}}
    else:
        data = {"route_kind": kind, "quote": message}
    data["schema_version"] = "primary-route-v1"
    data.setdefault("execution_scope", "none")
    data.setdefault("effect_intent", "none")
    return bind_route_signal(RouteSignal.model_validate(data), message,
                             saved_plan_present=saved, pending_action=pending,
                             pending_origin_verified=pending is not None)


def _forbidden(*args, **kwargs):
    raise AssertionError("AC2: retired semantic reviewer was called")


@pytest.mark.parametrize("routing_profile", ["auto", "unified"])
@pytest.mark.parametrize("scenario", [
    "create", "cancel", "write", "respond", "reject", "pending_mcp",
])
def test_unified_never_invokes_any_legacy_reviewer(
        tmp_path, monkeypatch, scenario, routing_profile):
    for name in LEGACY_REVIEWERS:
        monkeypatch.setattr("apps.api.main." + name, _forbidden)
    # The general Understanding and policy classifiers also cannot independently
    # reclassify an admitted Primary decision (AC1).
    monkeypatch.setattr("apps.api.main.UnderstandingService", _forbidden)
    monkeypatch.setattr("apps.api.main.decide_execution_policy_detail", _forbidden)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", _forbidden)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", _forbidden)

    called = []
    async def primary(*args, **kwargs):
        message = args[2]
        called.append(message)
        if message == CREATE:
            return _proposal("plan_create", message)
        if message == CANCEL:
            return _proposal("plan_cancel", message, saved=True)
        if message == WRITE:
            return _proposal("ordinary_tool", message)
        if message == RESPOND:
            return _proposal("respond", message)
        if message == REJECT:
            return _proposal("reject", message)
        if message == PENDING:
            assert kwargs["pending_action"] == "mcp_reference_lookup"
            return _proposal("clarify_response", message,
                             pending="mcp_reference_lookup")
        raise AssertionError("unexpected semantic request")
    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)

    class AnswerOnlyLoop:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, goal, **kwargs):
            assert kwargs["execution_mode"] == "respond_only"
            return {"run_id": "answer-only", "status": "completed",
                    "answer": "项目说明", "trace": [], "steps": 0}
    if scenario == "respond":
        monkeypatch.setattr("apps.api.main.AgentLoop", AnswerOnlyLoop)
    if scenario == "pending_mcp":
        monkeypatch.setattr("apps.api.main.execute_pending_readonly_v1",
                            lambda **kwargs: {
                                "run_id": kwargs["turn_id"],
                                "status": "completed",
                                "termination_reason": "verified_clarification_execution",
                                "answer": "已查询 MCP",
                                "trace": [], "steps": 1,
                            })

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / (scenario + '.db')}",
        model_provider="ollama_proxy", routing_profile=routing_profile)
    assert settings.primary_route_v1_active
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        if scenario == "pending_mcp":
            with Session.begin() as db:
                assistant = Message(conversation_id=cid, role="assistant",
                                    content="请提供查询词条", status="partial")
                db.add(assistant)
                db.flush()
                db.add(Turn(conversation_id=cid, request_id="ac2-pending-origin",
                            state="partial", assistant_message_id=assistant.id,
                            failure_code="needs_input:mcp_reference_lookup"))
        def send(message, rid):
            response = client.post(f"/conversations/{cid}/agent/messages",
                                   json={"content": message,
                                         "client_request_id": rid})
            assert response.status_code == 200, response.text
            return response.json()
        if scenario == "cancel":
            created = send(CREATE, "ac2-create-for-cancel")
            assert created["termination_reason"] == "plan_created_pending_execution"
        message = {
            "create": CREATE, "cancel": CANCEL, "write": WRITE,
            "respond": RESPOND, "reject": REJECT, "pending_mcp": PENDING,
        }[scenario]
        result = send(message, "ac2-route-" + scenario)
        expected = {
            "create": "plan_created_pending_execution",
            "cancel": "plan_cancelled",
            "write": "primary_v1_task_created",
            "reject": "primary_v1_route_rejected",
            "pending_mcp": "verified_clarification_execution",
        }
        if scenario in expected:
            assert result["termination_reason"] == expected[scenario]
        elif scenario == "respond":
            assert result["status"] == "completed"
        assert len(called) == (2 if scenario == "cancel" else 1)
