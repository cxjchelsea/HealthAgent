"""V3 semantic dimensions are verified before deterministic route compilation."""
import pytest
from agent.route_semantic_v3 import SemanticIntent, compile_semantic
from agent.route_shadow_host import ShadowHostFacts


def proposal(obj, op, effect, **kwargs):
    return SemanticIntent(object_type=obj, operation=op, effect_intent=effect, **kwargs)


def test_tool_invocation_remains_read_only_when_explicit():
    p=proposal("tool","invoke","read_only",action_kind="tool_call",
               persistence_intent="none",execution_intent="single_tool",
               context_relation="independent")
    compiled=compile_semantic(p,"查询数据",ShadowHostFacts(has_plan=False))
    assert (compiled.primary_route,compiled.effect_intent)==("agent_loop","read_only")


def test_tool_wrongly_tagged_task_record_is_rejected():
    p=proposal("task","create","read_only",action_kind="tool_call")
    with pytest.raises(ValueError,match="semantic_action_kind_conflict"):
        compile_semantic(p,"计算 4+4",ShadowHostFacts(has_plan=False))


def test_draft_only_blocks_persistent_creation():
    p=proposal("task","create","none",action_kind="task_record",
               persistence_intent="draft_only")
    with pytest.raises(ValueError,match="draft_must_not_mutate"):
        compile_semantic(p,"起草而不保存",ShadowHostFacts(has_plan=False))


def test_resume_with_first_step_only_is_rejected():
    p=proposal("plan","resume","read_only",action_kind="plan_management",
               execution_intent="first_step")
    with pytest.raises(ValueError,match="first_step_scope_conflict"):
        compile_semantic(p,"仅执行第一步",ShadowHostFacts(has_plan=True))


def test_first_step_compiles_to_first_step_route():
    p=proposal("plan","execute_first","read_only",action_kind="plan_management",
               execution_intent="first_step",context_relation="saved_plan")
    result=compile_semantic(p,"仅执行第一步",ShadowHostFacts(has_plan=True))
    assert (result.primary_route,result.execution_scope)==("plan_execute_first","first_step")


def test_pending_cancel_remains_non_effect():
    p=proposal("pending_slot","cancel","none",action_kind="pending_reply")
    result=compile_semantic(p,"取消",ShadowHostFacts(has_plan=False))
    assert (result.primary_route,result.effect_intent)==("cancel","none")


def test_pending_without_origin_stays_unavailable():
    p=proposal("pending_slot","invoke","read_only",action_kind="pending_reply")
    with pytest.raises(ValueError,match="pending_origin_unverified"):
        compile_semantic(p,"RAG",ShadowHostFacts(has_plan=False))


def test_verified_negative_quote_blocks_unscoped_positive_action():
    p=proposal("task","create","mutation_requested",negative_constraints=["不要保存"])
    with pytest.raises(ValueError,match="negative_constraint_requires_scope_review"):
        compile_semantic(p,"不要保存",ShadowHostFacts(has_plan=False))


def test_unverified_negative_quote_is_not_trusted():
    p=proposal("task","create","mutation_requested",negative_constraints=["some other words"])
    with pytest.raises(ValueError,match="unverified_quote"):
        compile_semantic(p,"不要保存",ShadowHostFacts(has_plan=False))
