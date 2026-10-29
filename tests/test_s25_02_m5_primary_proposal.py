"""M5 contract is semantic-only and rejects mismatched or forged evidence."""
import pytest
from pydantic import ValidationError

from agent.route_primary_m5 import PrimarySemanticProposal
from agent.route_primary_m5_binding import (
    ProposalBindingRefused, bind_primary_proposal,
)


def proposal(route="plan_execute_first", **overrides):
    action = {"route": route, "scope": "first_step",
              "evidence": [{"start": 0, "end": 4, "exact_text": "第一步"}]}
    fields = dict(schema_version="s25-m5-primary-v1", route=route,
                  scope="first_step", effect_intent="read_only",
                  ambiguity="clear", operation=action)
    fields.update(overrides)
    return fields


def test_first_step_binds_source_but_never_authorizes():
    p = PrimarySemanticProposal.model_validate(proposal())
    bound = bind_primary_proposal(p, "第一步执行")
    assert bound.route == "plan_execute_first"
    assert bound.scope == "first_step"
    assert bound.execution_authorized is False


def test_fake_source_span_rejected():
    p = PrimarySemanticProposal.model_validate(proposal())
    with pytest.raises(ProposalBindingRefused, match="m5_source_span_mismatch"):
        bind_primary_proposal(p, "第二步执行")


def test_wrong_operation_route_rejected():
    fields = proposal(operation={"route": "plan_cancel", "evidence": [
        {"start": 0, "end": 4, "exact_text": "第一步"}]})
    with pytest.raises(ValidationError, match="m5_operation_route_mismatch"):
        PrimarySemanticProposal.model_validate(fields)


def test_scope_widening_rejected():
    with pytest.raises(ValidationError, match="m5_invalid_scope"):
        PrimarySemanticProposal.model_validate(proposal(scope="all_steps"))


def test_extra_permission_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PrimarySemanticProposal.model_validate(proposal(execution_authorized=True))


def test_plan_creation_cannot_dispatch():
    action = {"route": "plan_create", "save_only": True, "steps": [
        {"goal": "计算1", "tool_name": "calculate",
         "arguments": {"expression": "2+3"},
         "evidence": [{"start": 0, "end": 4, "exact_text": "两步计划"}]},
        {"goal": "计算2", "tool_name": "calculate",
         "arguments": {"expression": "4+5"},
         "evidence": [{"start": 0, "end": 4, "exact_text": "两步计划"}]}]}
    p = PrimarySemanticProposal.model_validate(
        proposal(route="plan_create", scope="none",
                 effect_intent="mutation_requested", operation=action))
    bound = bind_primary_proposal(p, "两步计划只保存")
    assert bound.execution_authorized is False
    with pytest.raises(ValidationError):
        PrimarySemanticProposal.model_validate(
            proposal(route="plan_create", scope="none",
                     effect_intent="mutation_requested",
                     operation=action | {"save_only": False}))


def test_unknown_operation_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PrimarySemanticProposal.model_validate(
            proposal(operation=proposal()["operation"] | {"authorized": True}))


def test_clarify_requires_question():
    with pytest.raises(ValidationError, match="m5_missing_clarification"):
        PrimarySemanticProposal.model_validate(
            proposal(route="clarify", scope="none", effect_intent="none",
                     operation={"route": "clarify"}))
