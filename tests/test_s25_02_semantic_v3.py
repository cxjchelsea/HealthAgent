"""Semantic v3: interpretation is separate from deterministic compilation/Host."""
import asyncio
import json

import pytest

from agent.route_semantic_v3 import SemanticIntent, compile_semantic, evaluate_semantic_v3
from agent.route_shadow_host import ShadowHostFacts, preview_admission


def intent(obj, op, effect, **rest):
    return SemanticIntent.model_validate({
        "object_type":obj, "operation":op, "effect_intent":effect, **rest})


def facts(**kw):
    return ShadowHostFacts(has_plan=kw.pop("has_plan", False), **kw)


def test_single_task_is_not_plan_creation():
    out=compile_semantic(intent("task","create","mutation_requested"),
                         "帮我创建周五提交报告的任务", facts())
    assert (out.primary_route,out.route_target)==("agent_loop","tool_action")


def test_plan_requires_grounded_separate_steps():
    text="保存一个计划：先读资料，再计算结果"
    p=intent("plan","create","mutation_requested",
             source_quote="保存一个计划",
             plan_steps=["先读资料","再计算结果"])
    compiled=compile_semantic(p,text,facts())
    assert compiled.primary_route=="plan_create"
    assert len(compiled.plan_step_evidence)==2
    assert preview_admission(compiled,facts()).disposition=="needs_evidence"


def test_plan_missing_steps_stays_blocked():
    with pytest.raises(ValueError, match="plan_steps_unverified"):
        compile_semantic(intent("plan","create","mutation_requested"),
                         "创建一个计划",facts())


def test_revision_explicit_step_and_trusted_length_required():
    p=intent("plan_step","revise","mutation_requested",
             reference_text="第二步",step_number=2)
    trusted=facts(has_plan=True,revision_valid=True,plan_states=("pending","pending"))
    compiled=compile_semantic(p,"修改第二步",trusted)
    assert compiled.reference.step_number==2
    with pytest.raises(ValueError,match="plan_step_binding_unverified"):
        compile_semantic(p,"修改第二步",facts(has_plan=False))


def test_pending_cannot_invent_origin_or_effect():
    p=intent("pending_slot","invoke","read_only")
    with pytest.raises(ValueError,match="pending_origin_unverified"):
        compile_semantic(p,"RAG",facts())
    trusted=facts(pending_action="mcp_reference_lookup",pending_field="term",
                  pending_original_request_bound=True)
    assert compile_semantic(p,"RAG",trusted).reference.pending_field=="term"
    with pytest.raises(ValueError,match="pending_effect_unverified"):
        compile_semantic(intent("pending_slot","invoke","none"),"RAG",trusted)


def test_missing_plan_resume_is_host_denied_not_reclassified():
    p=intent("plan","resume","read_only")
    compiled=compile_semantic(p,"继续之前的计划",facts())
    assert compiled.primary_route=="plan_resume"
    assert preview_admission(compiled,facts()).reason=="missing_saved_plan"


def test_multilingual_step_reference_is_not_regex_dependent():
    trusted=facts(has_plan=True,revision_valid=True,plan_states=("pending","pending"))
    p=intent("plan_step","revise","mutation_requested",
             reference_text="step two",step_number=2)
    assert compile_semantic(p,"Please change step two",trusted).reference.step_number==2


def test_invalid_semantic_pair_never_repaired():
    with pytest.raises(ValueError,match="unsupported_semantic_pair"):
        compile_semantic(intent("task","resume","read_only"),"continue",facts())


def test_evaluator_does_not_authorize_changes():
    p=intent("task","create","mutation_requested")
    class Stub:
        async def complete(self, **kw):
            return p.model_dump_json()
    out=asyncio.run(evaluate_semantic_v3(Stub(),"stub","创建任务",facts()))
    assert out["state"]=="valid"
    assert out["host_admission_complete"] is False
    assert out["host_preview"]["disposition"]=="needs_evidence"
