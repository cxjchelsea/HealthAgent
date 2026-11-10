"""S25-06 E1: a single gated Primary proposal, never an execution permit."""
import asyncio
import json

import pytest
from pydantic import ValidationError

from agent.primary_route_v1 import (
    PrimaryRouteRejected, RouteSignal, bind_route_signal,
    primary_route_generation_schema, propose_primary_route,
)
def dependency_proposal():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询 RAG 定义", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "统计查到的定义", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ], "executions": {},
    }


MESSAGE = "请先查询 RAG 的定义，再统计查到的文本字符数。"


def _signal():
    return {
        "schema_version": "primary-route-v1",
        "route_kind": "plan_create", "quote": MESSAGE,
        "execution_scope": "none", "effect_intent": "mutation_requested",
        "steps": [], "dependency_plan": dependency_proposal(),
    }


def test_new_dependency_payload_is_valid_but_requires_opt_in_binding():
    route = RouteSignal.model_validate(_signal())
    assert route.steps == []
    with pytest.raises(PrimaryRouteRejected, match="primary_route_dependency_payload_denied"):
        bind_route_signal(route, MESSAGE, saved_plan_present=False,
                          pending_action=None)
    admitted = bind_route_signal(
        route, MESSAGE, saved_plan_present=False, pending_action=None,
        dependency_plan_enabled=True)
    assert admitted.dependency_plan is not None
    assert admitted.dependency_plan.steps[1].depends_on == [1]
    assert admitted.steps == []


@pytest.mark.parametrize("patch", [
    {"steps": [{"goal": "legacy", "tool_name": "calculate",
                "arguments": {"expression": "1+1"}, "quote": "RAG"}]},
    {"route_kind": "ordinary_tool", "effect_intent": "read_only"},
    {"route_kind": "plan_resume", "effect_intent": "read_only",
     "execution_scope": "remaining_steps"},
])
def test_dependency_payload_xor_other_routes_and_legacy_steps(patch):
    raw = _signal()
    raw.update(patch)
    with pytest.raises(ValidationError):
        RouteSignal.model_validate(raw)


@pytest.mark.parametrize("mutate", [
    lambda p: p["steps"][0]["argument_bindings"]["term"].update(
        {"source_quote": "not in the request"}),
    lambda p: p["steps"][1]["argument_bindings"]["text"].update(
        {"source_step_id": 8}),
    lambda p: p["steps"][1].update({"expected_tool": "create_task"}),
    lambda p: p.update({"revision": 2}),
])
def test_host_binding_fails_closed_for_untrusted_dependency_payload(mutate):
    raw = _signal()
    mutate(raw["dependency_plan"])
    try:
        signal = RouteSignal.model_validate(raw)
    except ValidationError:
        return
    with pytest.raises(PrimaryRouteRejected):
        bind_route_signal(signal, MESSAGE, saved_plan_present=False,
                          pending_action=None, dependency_plan_enabled=True)


def test_pending_and_existing_saved_plan_rejected():
    signal = RouteSignal.model_validate(_signal())
    with pytest.raises(PrimaryRouteRejected):
        bind_route_signal(signal, MESSAGE, saved_plan_present=True,
                          pending_action=None, dependency_plan_enabled=True)
    with pytest.raises(PrimaryRouteRejected, match="primary_route_dependency_payload_denied"):
        bind_route_signal(signal, MESSAGE, saved_plan_present=False,
                          pending_action="create_task", dependency_plan_enabled=True)


def test_legacy_create_remains_valid_with_opt_in_and_default_off():
    old = {
        "schema_version": "primary-route-v1", "route_kind": "plan_create",
        "quote": MESSAGE, "execution_scope": "none",
        "effect_intent": "mutation_requested", "dependency_plan": None,
        "steps": [
            {"goal": "calculate", "tool_name": "calculate",
             "arguments": {"expression": "RAG"}, "quote": "RAG"},
            {"goal": "lookup", "tool_name": "mcp_reference_lookup",
             "arguments": {"term": "RAG"}, "quote": "RAG"},
        ],
    }
    signal = RouteSignal.model_validate(old)
    for enabled in (False, True):
        bound = bind_route_signal(signal, MESSAGE, saved_plan_present=False,
                                  pending_action=None,
                                  dependency_plan_enabled=enabled)
        assert len(bound.steps) == 2
        assert bound.dependency_plan is None


def test_default_generation_schema_does_not_offer_dependency_payload():
    old = primary_route_generation_schema(MESSAGE)
    assert "dependency_plan" not in old["properties"]
    enabled = primary_route_generation_schema(
        MESSAGE, dependency_plan_enabled=True)
    assert "dependency_plan" in enabled["properties"]


def test_primary_generation_uses_one_model_call_and_optional_new_contract():
    class Fake:
        def __init__(self):
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            return json.dumps(_signal(), ensure_ascii=False)

    async def run(enabled):
        provider = Fake()
        if enabled:
            result = await propose_primary_route(
                provider, "fake-model", MESSAGE,
                saved_plan_present=False, dependency_plan_enabled=True)
            assert result.dependency_plan is not None
            assert len(provider.calls) == 1
            assert "dependency_plan" in provider.calls[0]["response_schema"]["properties"]
        else:
            with pytest.raises(PrimaryRouteRejected, match="primary_route_dependency_payload_denied"):
                await propose_primary_route(
                    provider, "fake-model", MESSAGE, saved_plan_present=False)
            assert len(provider.calls) == 1
            assert "dependency_plan" not in provider.calls[0]["response_schema"]["properties"]

    asyncio.run(run(True))
    asyncio.run(run(False))
