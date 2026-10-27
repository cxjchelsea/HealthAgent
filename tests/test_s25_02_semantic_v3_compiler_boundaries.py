"""V3 deterministic evidence and diagnostics never silently change semantic effects."""
import asyncio
from agent.route_semantic_v3 import SemanticIntent, compile_semantic, evaluate_semantic_v3
from agent.route_shadow_host import ShadowHostFacts


def test_plan_two_exact_steps_do_not_require_redundant_source_quote():
    message="制定计划，先读取资料，再计算结果"
    intent=SemanticIntent(object_type="plan",operation="create",
        effect_intent="mutation_requested",plan_steps=["先读取资料","再计算结果"])
    compiled=compile_semantic(intent,message,ShadowHostFacts(has_plan=False))
    assert compiled.primary_route=="plan_create"
    assert len(compiled.plan_step_evidence)==2
    assert len(compiled.source_evidence)==1
    assert compiled.source_evidence[0].exact_text in message


def test_internal_effect_is_compiled_and_model_disagreement_is_visible():
    intent=SemanticIntent(object_type="task",operation="create",
                          effect_intent="read_only")
    class Stub:
        async def complete(self,**kwargs):
            return intent.model_dump_json()
    out=asyncio.run(evaluate_semantic_v3(
        Stub(),"stub","请创建一个任务",ShadowHostFacts(has_plan=False)))
    assert out["state"]=="valid"
    assert out["semantic_object"]=="task"
    assert out["model_effect"]=="read_only"
    assert out["compiled_effect"]=="mutation_requested"
    assert out["effect_disagreement"] is True
    assert out["host_admission_complete"] is False
    assert out["host_preview"]["disposition"]=="needs_evidence"


def test_plan_source_quote_without_steps_never_admits():
    intent=SemanticIntent(object_type="plan",operation="create",
        effect_intent="mutation_requested",source_quote="制定计划")
    class Stub:
        async def complete(self,**kwargs):
            return intent.model_dump_json()
    out=asyncio.run(evaluate_semantic_v3(
        Stub(),"stub","制定计划",ShadowHostFacts(has_plan=False)))
    assert out["reason"]=="plan_steps_unverified"
    assert out["host_admission_complete"] is False
