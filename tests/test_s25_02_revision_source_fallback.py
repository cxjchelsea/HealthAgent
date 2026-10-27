"""Targeted revision source fallback: source proof only, never Host authority."""
import asyncio
from types import SimpleNamespace

from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_semantic_evidence import verify_core_evidence
from evals.s25_02_m2_evidence_structured_live import (
    evaluate, facts_for, proposal_from_core, extract_unique_ordinal,
)
from evals.s25_02_m2_hierarchical_boundary_cases import CASES


class Provider:
    def __init__(self, quote_response='{"current_turn_quote":null,"revision_step_quote":null}'):
        self.replies = iter(['{"object_type":"plan_step"}',
                             '{"operation":"revise"}', quote_response])
    async def complete(self, **kwargs):
        return next(self.replies)


def find(name):
    return next(c for c in CASES if c.case_id == name)


def test_revision_abstained_quote_uses_literal_trusted_message_with_checkpoint():
    c = find("modify_step")
    r = asyncio.run(evaluate(Provider(), "qwen3:14b", c, "positive", 5))
    assert r["core_correct"]
    assert r["quote_state"] == "trusted_full_message_revision"
    assert r["disposition"] == "potentially_admissible"
    assert r["host_authorization"] is False


def test_revision_missing_revision_still_blocks_even_with_source_fallback():
    c = find("modify_step")
    r = asyncio.run(evaluate(Provider(), "qwen3:14b", c, "boundary", 5))
    assert r["disposition"] != "potentially_admissible"
    assert r["host_authorization"] is False


def test_missing_ambiguous_or_out_of_range_step_cannot_gain_revision_authority():
    cases = [
        ("请修改第二步和第二步", ("pending", "pending"), True),
        ("请修改第三步", ("pending", "pending"), True),
        ("请修改第二步", ("pending", "pending"), False),
        ("请调整计划", ("pending", "pending"), True),
    ]
    from dataclasses import replace
    from agent.route_shadow_host import ShadowHostFacts
    for message, states, revision_valid in cases:
        step = extract_unique_ordinal(message)
        core = HierarchicalDiagnostic("plan_step", "revise")
        route = proposal_from_core(core, message, message, step)
        facts = ShadowHostFacts(has_plan=True, plan_states=states,
            revision_valid=revision_valid, bound_step_valid=True,
            current_write_authorized=True, source_provenance_valid=True)
        result = verify_core_evidence(core, route, message, facts)
        assert result.disposition != "potentially_admissible"
        assert result.host_authorization is False


def test_modify_but_do_not_execute_remains_revision_only():
    message = "把已保存计划第二步改成新说明，但不要执行"
    core = HierarchicalDiagnostic("plan_step", "revise")
    route = proposal_from_core(core, message, message,
                               extract_unique_ordinal(message))
    from agent.route_shadow_host import ShadowHostFacts
    facts = ShadowHostFacts(has_plan=True, plan_states=("pending", "pending"),
        revision_valid=True, bound_step_valid=True,
        current_write_authorized=True, source_provenance_valid=True)
    r = verify_core_evidence(core, route, message, facts)
    assert route.primary_route == "plan_revise"
    assert r.host_authorization is False
    assert r.disposition == "potentially_admissible"
