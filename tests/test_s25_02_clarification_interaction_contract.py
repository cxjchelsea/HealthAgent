"""Contract checks only; cannot replace real Qwen or independent blind scores."""
from agent.route_semantic_hierarchical import (
    OBJECT_PROMPT, OPERATION_BY_OBJECT, NoneOperation, PlanStepOperation,
)
from agent.route_semantic_preview import ROUTE_BY_CORE
from evals.s25_02_m2_followup_sealed_v2 import assert_frozen

def test_clarification_semantics_precede_business_nouns():
    assert "NEXT INTERACTION" in OBJECT_PROMPT
    assert "request TO CLARIFY" in OBJECT_PROMPT
    assert "business target" in OBJECT_PROMPT
    assert "没有说明需要改动存档计划中的哪一步" not in OBJECT_PROMPT

def test_business_route_contract_not_extended_by_clarify_fix():
    assert set(NoneOperation.model_fields["operation"].annotation.__args__) == {"answer", "clarify"}
    assert set(PlanStepOperation.model_fields["operation"].annotation.__args__) == {"revise"}
    assert ROUTE_BY_CORE[("none","clarify")] == "clarify"
    assert ROUTE_BY_CORE[("plan_step","revise")] == "plan_revise"
    assert OPERATION_BY_OBJECT["plan_step"] is PlanStepOperation

def test_known_development_cohort_remains_sealed():
    assert_frozen()
