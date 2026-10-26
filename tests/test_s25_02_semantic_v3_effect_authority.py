"""Effect derivation never grants tool or persistence authority."""
from agent.route_semantic_v3 import SemanticIntent, compile_semantic
from agent.route_shadow_host import ShadowHostFacts, preview_admission


def intent(obj, op, effect, **extras):
    return SemanticIntent(object_type=obj, operation=op, effect_intent=effect, **extras)


def test_plan_create_effect_is_write_even_when_model_says_none():
    msg="保存计划，先查询资料，再计算"
    result=compile_semantic(intent("plan","create","none",
                            plan_steps=["先查询资料","再计算"]),msg,
                            ShadowHostFacts(has_plan=False))
    assert result.effect_intent=="mutation_requested"
    assert preview_admission(result,ShadowHostFacts(has_plan=False)).disposition=="needs_evidence"


def test_step_revision_uses_durable_write_effect():
    msg="修改第二步"
    facts=ShadowHostFacts(has_plan=True,plan_states=("pending","pending"),
                          revision_valid=True)
    result=compile_semantic(intent("plan_step","revise","none",
         step_number=2,reference_text="第二步"),msg,facts)
    assert result.effect_intent=="mutation_requested"
    assert preview_admission(result,facts).disposition=="needs_evidence"


def test_unknown_tool_effect_is_not_rewritten_to_readonly():
    result=compile_semantic(intent("tool","invoke","mutation_requested"),
        "请求工具", ShadowHostFacts(has_plan=False))
    assert result.effect_intent=="mutation_requested"
    assert preview_admission(result,ShadowHostFacts(has_plan=False)).disposition=="needs_evidence"


def test_unbound_pending_none_still_denied():
    import pytest
    facts=ShadowHostFacts(has_plan=False,pending_action="lookup",
        pending_field="term",pending_original_request_bound=True)
    with pytest.raises(ValueError,match="pending_effect_unverified"):
        compile_semantic(intent("pending_slot","invoke","none"),"RAG",facts)
