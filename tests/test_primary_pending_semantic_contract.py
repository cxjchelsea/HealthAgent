"""Primary Pending semantic contract: priority is current intent, not state.

The tests here check prompt input/constraints and non-reclassifying Host fence.
True semantic accuracy is evaluated separately with the real-model contrast
runner, never inferred from these deterministic tests alone.
"""

import pytest
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


def test_verified_pending_context_gets_single_current_turn_semantic_instruction():
    import asyncio
    import json

    from agent.primary_route_v1 import propose_primary_route, _PENDING_CURRENT_TURN_SYSTEM

    class Provider:
        def __init__(self):
            self.calls = []

        async def complete(self, *, model, messages, response_schema):
            self.calls.append((model, messages, response_schema))
            msg = json.loads(messages[-1]["content"])["current_request"]
            return json.dumps({
                "schema_version": "primary-route-v1",
                "route_kind": "respond",
                "quote": msg,
                "execution_scope": "none",
                "effect_intent": "none",
            }, ensure_ascii=False)

    p = Provider()
    message = "给我介绍一下知识图谱的作用"
    result = asyncio.run(propose_primary_route(
        p, "synthetic", message, saved_plan_present=False,
        pending_action="create_task", pending_origin_verified=True,
        pending_question="请补充待办任务名称。",
        recent_context=[{"role": "assistant", "content": "请补充待办任务名称。"}]))
    assert result.route_kind == "respond"
    assert len(p.calls) == 1
    model, messages, schema = p.calls[0]
    assert len(messages) == 3
    assert messages[1]["role"] == "system"
    assert messages[1]["content"] == _PENDING_CURRENT_TURN_SYSTEM
    assert "counterfactual" in messages[1]["content"].lower()
    assert json.loads(messages[2]["content"])["current_request"] == message
    assert schema["properties"]["quote"]["const"] == message
    assert "clarify_response" in schema["properties"]["route_kind"]["enum"]


def test_no_verified_pending_keeps_original_one_system_one_user_contract():
    import asyncio
    import json
    from agent.primary_route_v1 import propose_primary_route

    class Provider:
        def __init__(self):
            self.calls = []

        async def complete(self, *, model, messages, response_schema):
            self.calls.append(messages)
            msg = json.loads(messages[-1]["content"])["current_request"]
            return json.dumps({
                "schema_version": "primary-route-v1",
                "route_kind": "respond",
                "quote": msg,
            }, ensure_ascii=False)

    p = Provider()
    result = asyncio.run(propose_primary_route(
        p, "synthetic", "解释多智能体通信",
        saved_plan_present=False, pending_action=None,
        pending_origin_verified=False))
    assert result.route_kind == "respond"
    assert len(p.calls) == 1
    assert [x["role"] for x in p.calls[0]] == ["system", "user"]


def test_missing_mcp_term_prompt_contract_is_exclusive_and_host_validator_remains_strict():
    from agent.primary_route_v1 import _PRIMARY_SYSTEM, RouteSignal
    from pydantic import ValidationError
    assert "For a NEW MCP lookup that is missing its search term" in _PRIMARY_SYSTEM
    assert "pending_write_tool_name=null" in _PRIMARY_SYSTEM
    assert "Never combine pending_read_tool_name with pending_write_tool_name" in _PRIMARY_SYSTEM
    base = {
        "schema_version": "primary-route-v1",
        "route_kind": "clarify",
        "quote": "查术语参考释义",
        "execution_scope": "none",
        "effect_intent": "none",
        "steps": [],
        "clarification_question": "需要查询哪个术语？",
        "pending_read_tool_name": "mcp_reference_lookup",
        "pending_write_tool_name": None,
    }
    assert RouteSignal.model_validate(base).pending_read_tool_name == "mcp_reference_lookup"
    with pytest.raises(ValidationError):
        RouteSignal.model_validate({**base, "pending_write_tool_name": "create_task"})
    with pytest.raises(ValidationError):
        RouteSignal.model_validate({
            **base, "route_kind": "respond", "clarification_question": None})


def test_verified_mcp_pending_gets_its_own_term_semantic_contract():
    import asyncio
    import json
    from agent.primary_route_v1 import (
        _PRIMARY_MCP_FOCUSED_SYSTEM,
        _PENDING_CURRENT_TURN_SYSTEM,
        propose_primary_route,
    )

    class Provider:
        def __init__(self):
            self.calls = []

        async def complete(self, *, model, messages, response_schema):
            self.calls.append((messages, response_schema))
            current = json.loads(messages[-1]["content"])["current_request"]
            return json.dumps({
                "schema_version": "primary-route-v1",
                "route_kind": "clarify_response",
                "quote": current,
                "execution_scope": "none", "effect_intent": "none",
                "steps": [], "clarification_question": None,
                "pending_read_tool_name": None,
                "pending_write_tool_name": None,
            }, ensure_ascii=False)

    provider = Provider()
    route = asyncio.run(propose_primary_route(
        provider, "synthetic", "GraphRAG",
        saved_plan_present=False,
        pending_action="mcp_reference_lookup",
        pending_origin_verified=True,
        pending_question="想查询哪个术语？",
        recent_context=[{"role": "assistant", "content": "想查询哪个术语？"}]))
    assert route.route_kind == "clarify_response"
    assert len(provider.calls) == 1
    messages, schema = provider.calls[0]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == _PRIMARY_MCP_FOCUSED_SYSTEM
    assert messages[0]["content"] != _PENDING_CURRENT_TURN_SYSTEM
    assert "single abbreviation or short noun" in messages[0]["content"]
    assert "does NOT grant write authority" in messages[0]["content"]
    assert json.loads(messages[1]["content"])["pending_action"] == "mcp_reference_lookup"
    assert schema["properties"]["pending_read_tool_name"]["type"] == "null"
    assert schema["properties"]["route_kind"].get("const") is None


def test_mcp_pending_does_not_force_reply_route_in_schema():
    from agent.primary_route_v1 import primary_route_generation_schema
    schema = primary_route_generation_schema(
        "请先介绍一下什么是工作流调度",
        pending_origin_verified=True)
    assert schema["properties"]["route_kind"].get("const") is None
    assert "respond" in schema["properties"]["route_kind"]["enum"]
    assert "clarify" in schema["properties"]["route_kind"]["enum"]
    assert "clarify_response" in schema["properties"]["route_kind"]["enum"]
    assert schema["properties"]["pending_read_tool_name"]["type"] == "null"
