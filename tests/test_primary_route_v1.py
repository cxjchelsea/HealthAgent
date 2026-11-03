"""Contract-level tests for work package 1 (no provider/network/tool execution)."""
import json

import pytest
from pydantic import ValidationError

from agent.primary_route_v1 import (
    ROUTE_SCHEMA_VERSION, RouteSignal, PrimaryRouteRejected,
    adapt_legacy_route, bind_route_signal, propose_primary_route,
)


def signal(route_kind="respond", quote="你好", **kwargs):
    body = {
        "schema_version": ROUTE_SCHEMA_VERSION, "route_kind": route_kind,
        "quote": quote, "execution_scope": "none", "effect_intent": "none",
        "steps": [],
    }
    body.update(kwargs)
    return body


class Provider:
    def __init__(self, body):
        self.body = body
        self.calls = 0
        self.last_schema = None

    async def complete(self, *, model, messages, response_schema):
        self.calls += 1
        self.last_schema = response_schema
        return json.dumps(self.body, ensure_ascii=False)


@pytest.mark.parametrize("wrong", [
    {"schema_version": "s25-m5-primary-v1"},
    {"route_kind": "other"},
    {"route_kind": "execute_first"},
    {"unexpected": 1},
    {"execution_scope": "single_tool"},
    {"steps": [{"goal": "bad", "tool_name": "calculate",
                "arguments": {"expression": "2+2"}, "quote": "2+2"}]},
])
def test_schema_rejects_wrong_version_alias_and_payload(wrong):
    with pytest.raises(ValidationError):
        RouteSignal.model_validate(signal(**wrong))


@pytest.mark.parametrize("legacy,canonical", [
    ("create", "plan_create"), ("revise", "plan_revise"),
    ("execute_first", "plan_execute_first"), ("resume", "plan_resume"),
    ("cancel", "plan_cancel"), ("plan_only", "plan_create"),
])
def test_legacy_adapter_is_explicit(legacy, canonical):
    assert adapt_legacy_route(legacy) == canonical


@pytest.mark.parametrize("unmapped", ["other", "agent_loop", "respond", "ordinary_tool", ""])
def test_legacy_adapter_must_not_guess(unmapped):
    with pytest.raises(PrimaryRouteRejected, match="primary_route_legacy_unmapped"):
        adapt_legacy_route(unmapped)


def test_exact_unique_evidence_not_offsets_from_model():
    payload = RouteSignal.model_validate(signal())
    assert bind_route_signal(payload, "请说你好", saved_plan_present=False,
                             pending_action=None).evidence.start == 2
    with pytest.raises(PrimaryRouteRejected, match="primary_route_quote_ambiguous"):
        bind_route_signal(payload, "你好你好", saved_plan_present=False,
                          pending_action=None)
    with pytest.raises(PrimaryRouteRejected, match="primary_route_quote_not_found"):
        bind_route_signal(payload, "再见", saved_plan_present=False, pending_action=None)


def test_plan_route_never_creates_own_checkpoint():
    payload = RouteSignal.model_validate(signal(
        "plan_resume", "继续执行", execution_scope="remaining_steps",
        effect_intent="read_only"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_missing_saved_plan"):
        bind_route_signal(payload, "继续执行", saved_plan_present=False,
                          pending_action=None)
    approved_as_proposal = bind_route_signal(
        payload, "继续执行", saved_plan_present=True, pending_action=None)
    assert approved_as_proposal.route_kind == "plan_resume"
    assert not hasattr(approved_as_proposal, "admission_result")


def test_saved_plan_must_not_capture_ordinary_request():
    payload = RouteSignal.model_validate(signal(
        "ordinary_tool", "请计算", execution_scope="single_tool",
        effect_intent="read_only"))
    bound = bind_route_signal(payload, "请计算 1+2", saved_plan_present=True,
                              pending_action=None)
    assert bound.route_kind == "ordinary_tool"


def test_pending_reply_requires_durable_original_request():
    payload = RouteSignal.model_validate(signal(
        "clarify_response", "MCP"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_untrusted_pending_origin"):
        bind_route_signal(payload, "MCP", saved_plan_present=False,
                          pending_action="mcp_reference_lookup")
    bound = bind_route_signal(
        payload, "MCP", saved_plan_present=False,
        pending_action="mcp_reference_lookup", pending_origin_verified=True)
    assert bound.route_kind == "clarify_response"


def test_plan_create_requires_grounded_steps_and_only_saves():
    phrase = "只保存两步：计算3+4，再查询MCP"
    payload = RouteSignal.model_validate(signal(
        "plan_create", "只保存两步", effect_intent="mutation_requested",
        steps=[
            {"goal": "计算", "tool_name": "calculate",
             "arguments": {"expression": "3+4"}, "quote": "计算3+4"},
            {"goal": "查询", "tool_name": "mcp_reference_lookup",
             "arguments": {"term": "MCP"}, "quote": "查询MCP"},
        ]))
    b = bind_route_signal(payload, phrase, saved_plan_present=False,
                          pending_action=None)
    assert b.route_kind == "plan_create" and len(b.steps) == 2
    with pytest.raises(PrimaryRouteRejected, match="primary_route_unbound_step_args"):
        bind_route_signal(payload.model_copy(update={
            "steps": [payload.steps[0].model_copy(update={
                "arguments": {"expression": "9+9"}}), payload.steps[1]]}),
            phrase, saved_plan_present=False, pending_action=None)


def test_revision_needs_host_verified_step_number():
    payload = RouteSignal.model_validate(signal(
        "plan_revise", "修改第二步", effect_intent="mutation_requested",
        step_number=2))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_unbound_step"):
        bind_route_signal(payload, "修改第二步", saved_plan_present=True,
                          pending_action=None, saved_step_count=1)
    result = bind_route_signal(payload, "修改第二步", saved_plan_present=True,
                               pending_action=None, saved_step_count=2)
    assert result.step_number == 2


@pytest.mark.asyncio
async def test_exactly_one_semantic_model_call_no_legacy_fallback():
    provider = Provider(signal(
        "ordinary_tool", "请计算", execution_scope="single_tool",
        effect_intent="read_only"))
    route = await propose_primary_route(provider, "model", "请计算 2+3",
                                        saved_plan_present=False)
    assert route.route_kind == "ordinary_tool"
    assert provider.calls == 1
    assert provider.last_schema["properties"]["schema_version"]


@pytest.mark.asyncio
async def test_model_invalid_output_fails_closed_without_retry():
    provider = Provider(signal(route_kind="other"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_invalid_signal"):
        await propose_primary_route(provider, "model", "你好",
                                    saved_plan_present=False)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_model_plan_evidence_invalid_fails_closed():
    provider = Provider(signal(
        "plan_cancel", "取消不存在的文字", effect_intent="mutation_requested"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_quote_not_found"):
        await propose_primary_route(provider, "model", "取消当前计划",
                                    saved_plan_present=True)
    assert provider.calls == 1
