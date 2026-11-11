"""E3 Qwen invalid-shape correction is bounded and never bypasses Host."""
import asyncio
import json

import pytest

from agent.primary_route_v1 import (
    _is_missing_dependency_create_shape, propose_primary_route,
    PrimaryRouteRejected,
)

MESSAGE = (
    "请先使用 MCP 查询 RAG 的定义，然后把第一步查询结果中的 definition 原文"
    "传给 text_stats 统计字符数。先保存这两步依赖计划，不要执行。"
)

BAD = {
    "schema_version": "primary-route-v1",
    "route_kind": "plan_create", "quote": MESSAGE,
    "execution_scope": "none", "effect_intent": "mutation_requested",
    "steps": [],
}

GOOD = {
    **BAD,
    "dependency_plan": {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {
                "id": 1, "goal": "查询 RAG", "depends_on": [],
                "status": "pending", "expected_tool": "mcp_reference_lookup",
                "tool_version": "1.0.0",
                "argument_bindings": {
                    "term": {"kind": "literal", "value": "RAG",
                             "source_quote": "RAG"},
                },
            },
            {
                "id": 2, "goal": "统计 definition", "depends_on": [1],
                "status": "pending", "expected_tool": "text_stats",
                "tool_version": "1.0.0",
                "argument_bindings": {
                    "text": {"kind": "observation_ref", "source_step_id": 1,
                             "field": "definition"},
                },
            },
        ],
        "executions": {},
    },
}

class TwoReplies:
    def __init__(self, *replies):
        self.replies = replies
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.replies[len(self.calls)-1], ensure_ascii=False)


def route(provider, *, enabled):
    return asyncio.run(propose_primary_route(
        provider, "test-provider", MESSAGE,
        saved_plan_present=False, dependency_plan_enabled=enabled))


def test_bad_new_plan_shape_is_bounded_retry_candidate():
    assert _is_missing_dependency_create_shape(json.dumps(BAD))
    assert _is_missing_dependency_create_shape(json.dumps({
        **BAD, "dependency_plan": None}))
    for payload in (
        {**BAD, "steps": [{}]},
        {**BAD, "route_kind": "respond"},
        {**BAD, "dependency_plan": {}},
    ):
        assert not _is_missing_dependency_create_shape(json.dumps(payload))
    assert not _is_missing_dependency_create_shape("not json")


def test_enabled_e3_can_regenerate_complete_model_proposal_once():
    provider = TwoReplies(BAD, GOOD)
    proposal = route(provider, enabled=True)
    assert len(provider.calls) == 2
    assert proposal.dependency_plan is not None
    assert proposal.steps == []
    # Both are normal provider invocations against the same schema.
    assert provider.calls[0]["response_schema"] == provider.calls[1]["response_schema"]
    assert "dependency_plan" in provider.calls[0]["response_schema"]["properties"]


def test_enabled_e3_remains_refused_after_second_invalid_proposal():
    provider = TwoReplies(BAD, BAD)
    with pytest.raises(PrimaryRouteRejected, match="primary_route_invalid_signal"):
        route(provider, enabled=True)
    assert len(provider.calls) == 2


def test_disabled_e3_must_not_retry_or_advertise_dependency_schema():
    provider = TwoReplies(BAD)
    with pytest.raises(PrimaryRouteRejected, match="primary_route_invalid_signal"):
        route(provider, enabled=False)
    assert len(provider.calls) == 1
    assert "dependency_plan" not in provider.calls[0]["response_schema"]["properties"]


def test_do_not_retry_other_invalid_schema_conditions():
    bad_other = {**BAD, "route_kind": "respond"}
    provider = TwoReplies(bad_other)
    with pytest.raises(PrimaryRouteRejected, match="primary_route_invalid_signal"):
        route(provider, enabled=True)
    assert len(provider.calls) == 1



def test_e3_generation_schema_requires_payload_key_without_granting_it():
    from jsonschema import Draft202012Validator
    from agent.primary_route_v1 import primary_route_generation_schema

    opted_in = primary_route_generation_schema(
        MESSAGE, dependency_plan_enabled=True)
    Draft202012Validator.check_schema(opted_in)
    assert "dependency_plan" in opted_in["properties"]
    assert "dependency_plan" in opted_in["required"]
    with pytest.raises(Exception):
        Draft202012Validator(opted_in).validate(BAD)
    Draft202012Validator(opted_in).validate(GOOD)

    for kwargs in (
        {},
        {"dependency_plan_enabled": True, "saved_plan_present": True},
        {"dependency_plan_enabled": True, "pending_action_present": True},
        {"dependency_plan_enabled": True, "pending_origin_verified": True},
    ):
        schema = primary_route_generation_schema(MESSAGE, **kwargs)
        assert "dependency_plan" not in schema["properties"]
        assert "dependency_plan" not in schema.get("required", [])



def test_e3_generation_limits_argument_binding_names_without_host_bypass():
    from jsonschema import Draft202012Validator
    from agent.primary_route_v1 import primary_route_generation_schema

    schema = primary_route_generation_schema(
        MESSAGE, dependency_plan_enabled=True)
    Draft202012Validator.check_schema(schema)
    binding_schema = schema["$defs"]["DependencyStep"]["properties"][
        "argument_bindings"]
    assert binding_schema["additionalProperties"] is False
    assert set(binding_schema["properties"]) == {"term", "text"}
    assert binding_schema["minProperties"] == 1
    assert binding_schema["maxProperties"] == 1
    validator = Draft202012Validator(schema)
    validator.validate(GOOD)
    invalid_key = json.loads(json.dumps(GOOD))
    invalid_key["dependency_plan"]["steps"][0]["argument_bindings"] = {
        "query": {"kind": "literal", "value": "RAG", "source_quote": "RAG"},
    }
    assert not validator.is_valid(invalid_key)
    two_keys = json.loads(json.dumps(GOOD))
    two_keys["dependency_plan"]["steps"][0]["argument_bindings"]["text"] = {
        "kind": "literal", "value": "RAG", "source_quote": "RAG",
    }
    assert not validator.is_valid(two_keys)
    # Schema alone is not per-tool authority: Host rejects wrong-key
    # proposals even if a JSON Schema validator accepts the key name.
    from agent.primary_route_v1 import RouteSignal, bind_route_signal
    from agent.primary_route_v1 import PrimaryRouteRejected
    wrong_tool_key = json.loads(json.dumps(GOOD))
    wrong_tool_key["dependency_plan"]["steps"][0]["argument_bindings"] = {
        "text": {"kind": "literal", "value": "RAG", "source_quote": "RAG"},
    }
    assert validator.is_valid(wrong_tool_key)
    with pytest.raises(PrimaryRouteRejected, match="dependency_invalid_arguments"):
        bind_route_signal(
            RouteSignal.model_validate(wrong_tool_key), MESSAGE,
            saved_plan_present=False, pending_action=None,
            dependency_plan_enabled=True)


def test_default_off_and_pending_schema_never_exposes_dependency_bindings():
    from agent.primary_route_v1 import primary_route_generation_schema

    for kwargs in ({}, {"dependency_plan_enabled": True,
                        "saved_plan_present": True},
                   {"dependency_plan_enabled": True,
                    "pending_action_present": True}):
        schema = primary_route_generation_schema(MESSAGE, **kwargs)
        assert "dependency_plan" not in schema["properties"]
        assert "DependencyStep" not in schema.get("$defs", {})
