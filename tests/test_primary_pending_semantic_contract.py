"""Primary Pending semantic contract: priority is current intent, not state.

The tests here check prompt input/constraints and non-reclassifying Host fence.
True semantic accuracy is evaluated separately with the real-model contrast
runner, never inferred from these deterministic tests alone.
"""
from agent.primary_route_v1 import (
    _PRIMARY_SYSTEM, RouteSignal, bind_route_signal,
    primary_route_generation_schema,
)
from agent.primary_host_admission_v1 import (
    PrimaryHostFacts, admit_primary_route_v1,
)


def test_primary_pending_prompt_prioritizes_current_user_request():
    for contract in (
        "PENDING CURRENT-TURN PRIORITY",
        "Interpret current_request as a NEW autonomous user message first",
        "pending_question is historical context",
        "CONTRAST A", "CONTRAST B", "CONTRAST C", "CONTRAST D", "CONTRAST E",
    ):
        assert contract in _PRIMARY_SYSTEM


def test_pending_generation_schema_never_forces_clarify_response():
    message = "请解释什么是事件驱动架构以及它的用途"
    schema = primary_route_generation_schema(
        message, pending_origin_verified=True)
    assert schema["properties"]["quote"]["const"] == message
    kind = schema["properties"]["route_kind"]
    assert kind.get("const") != "clarify_response"
    assert "clarify_response" in kind.get("enum", [])


def test_host_cannot_rewrite_wrong_pending_route_into_semantic_success():
    message = "我们换个话题，简单介绍一下模型上下文协议是什么。"
    proposal = bind_route_signal(
        RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "clarify_response",
            "quote": message,
        }), message, saved_plan_present=False,
        pending_action="create_task", pending_origin_verified=True)
    decision = admit_primary_route_v1(proposal, PrimaryHostFacts(
        checkpoint=None, pending_action="create_task",
        pending_origin_verified=True, pending_write_authority_verified=True,
        guarded_mode=True, turn_created=True, current_request=message))
    assert proposal.route_kind == "clarify_response"
    assert decision.route_kind == "clarify_response"
    assert decision.disposition == "restrict"
