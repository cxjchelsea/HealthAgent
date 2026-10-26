"""M2 provenance failures preserve only bounded semantic diagnostic fields."""
import asyncio
import json

from agent.route_shadow import evaluate_shadow


class Stub:
    def __init__(self, response):
        self.response = response

    async def complete(self, **kwargs):
        return json.dumps(self.response)


def case(**overrides):
    base = {
        "schema_version": "s25-route-v2",
        "primary_route": "plan_create",
        "route_target": "none",
        "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "ambiguity": "clear",
    }
    base.update(overrides)
    return base


def test_missing_plan_step_evidence_retains_bounded_semantics():
    result = asyncio.run(evaluate_shadow(
        Stub(case()), "stub", "先查资料然后计算", has_plan=False,
        pending_action=None))
    assert result["state"] == "unavailable"
    assert result["semantic_schema_valid"] is True
    assert result["semantic_proposal"]["route"] == "plan_create"
    assert result["semantic_proposal"]["effect"] == "mutation_requested"
    assert result["provenance_status"] == "HOLD"
    assert result["host_admission_complete"] is False
    assert "host_preview" not in result
    assert result["validation_code"] == "shadow_plan_steps_ungrounded"


def test_source_mismatch_keeps_semantics_but_not_user_source():
    result = asyncio.run(evaluate_shadow(
        Stub(case(source_evidence=[{"start":0,"end":3,
                                    "exact_text":"SENSITIVE_TOKEN"}])),
        "stub", "真实用户消息", has_plan=False,pending_action=None))
    assert result["semantic_proposal"]["route"] == "plan_create"
    assert "SENSITIVE_TOKEN" not in json.dumps(result)
    assert result["provenance_status"] == "HOLD"


def test_invalid_schema_has_no_valid_semantic_proposal():
    result = asyncio.run(evaluate_shadow(
        Stub(case(effect_intent="none")), "stub", "测试",
        has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"
    assert not result.get("semantic_proposal")
    assert result.get("semantic_schema_valid") is not True
