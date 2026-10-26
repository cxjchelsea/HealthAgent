"""Experiment comparison invariants and isolated outputs."""
from agent.route_semantic_ab import CoreIntent
from agent.route_semantic_ab_reference import needed, EvidenceDraft
from evals.s25_02_m2_ab_comparison import core_match
from evals.s25_02_m2_shadow_live import CASES

def test_stage_b_only_extracts_when_needed():
    assert needed(CoreIntent(object_type="plan",operation="create"))
    assert needed(CoreIntent(object_type="plan_step",operation="revise"))
    assert not needed(CoreIntent(object_type="tool",operation="invoke"))
    assert not needed(CoreIntent(object_type="pending_slot",operation="cancel"))

def test_reference_schema_has_no_authority_field():
    assert "permission" not in EvidenceDraft.model_fields
    assert "authorized" not in EvidenceDraft.model_fields

def test_core_metrics_distinguish_task_and_readonly_tool():
    task=next(x for x in CASES if x.case_id=="explicit_task")
    math=next(x for x in CASES if x.case_id=="standalone_math")
    assert core_match(task,"task","create")
    assert not core_match(math,"task","create")
    assert core_match(math,"tool","invoke")

def test_core_cancel_and_resume_are_distinct():
    cancel=next(x for x in CASES if x.case_id=="cancel_pending")
    resume=next(x for x in CASES if x.case_id=="resume_plan")
    assert core_match(cancel,"pending_slot","cancel")
    assert not core_match(cancel,"pending_slot","invoke")
    assert core_match(resume,"plan","resume")
    assert not core_match(resume,"plan","execute_first")
