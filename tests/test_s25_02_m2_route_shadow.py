"""M2 shadow must never mutate the real Host decision."""
import asyncio
import json
import pytest
from agent.route_shadow import ShadowRoute, evaluate_shadow


class Stub:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return json.dumps(self.data)


def route(name="agent_loop", **kwargs):
    base = {
        "schema_version": "s25-route-v2", "primary_route": name,
        "route_target": "tool_action", "execution_scope": "single_tool",
        "effect_intent": "read_only", "ambiguity": "clear",
        "source_quote": "计算 19+23", "goal": None, "goal_change": None,
    }
    base.update(kwargs)
    return base


def test_valid_semantic_shadow_only_reports_route():
    shadow = Stub(route())
    result = asyncio.run(evaluate_shadow(
        shadow, "fake", "请计算 19+23", has_plan=True, pending_action=None))
    assert result["state"] == "valid"
    assert result["route"] == "agent_loop"
    assert result["host_would_deny"] is None
    assert shadow.calls == 1


def test_missing_checkpoint_host_would_deny_without_execution():
    d = route("plan_resume", route_target="saved_plan",
              execution_scope="remaining_steps")
    shadow = Stub(d)
    result = asyncio.run(evaluate_shadow(
        shadow, "fake", "请计算 19+23", has_plan=False, pending_action=None))
    assert result["host_would_deny"] == "missing_saved_plan"


@pytest.mark.parametrize("data", [
    route("respond"),  # invalid respond target/effect/scope
    route(ambiguity="needs_disambiguation"),
    route(source_quote="模型虚构原文"),
])
def test_invalid_or_unbound_proposal_fails_closed(data):
    result = asyncio.run(evaluate_shadow(
        Stub(data), "fake", "请计算 19+23",
        has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"
    assert "route" not in result


def test_provider_failure_is_diagnostic_not_exception():
    result = asyncio.run(evaluate_shadow(
        Stub(error=RuntimeError("no response")), "fake",
        "请计算 19+23", has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"


def test_shadow_schema_rejects_unknown_fields():
    with pytest.raises(ValueError):
        ShadowRoute.model_validate({**route(), "authorized": True})
