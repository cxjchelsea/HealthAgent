"""Hierarchical classifier respects diagnostic-only separation."""
from agent.route_semantic_hierarchical import ObjectChoice, OperationChoice
from evals.s25_02_m2_ab_comparison import CASES, core_match


def test_object_and_operation_schemas_separate():
    assert "operation" not in ObjectChoice.model_fields
    assert "object_type" not in OperationChoice.model_fields


def test_hierarchical_comparison_expected_targets():
    plan=next(c for c in CASES if c.case_id=="plan_revision")
    assert core_match(plan,"plan_step","revise")
    assert not core_match(plan,"task","revise")


def test_no_authority_fields_in_classifier():
    for cls in (ObjectChoice, OperationChoice):
        assert "host_authorization" not in cls.model_fields
        assert "authorized" not in cls.model_fields
