"""M2 shadow must never mutate the real Host decision."""
import asyncio
import json
import pytest
from agent.route_shadow import ShadowRoute, evaluate_shadow


class Stub:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return json.dumps(self.data)


def route(name="agent_loop", **kwargs):
    base = {
        "schema_version": "s25-route-v2", "primary_route": name,
        "route_target": "tool_action", "execution_scope": "single_tool",
        "effect_intent": "read_only", "ambiguity": "clear",
        "source_quote": "计算 19+23", "goal": None, "goal_change": None,
    }
    base.update(kwargs)
    return base


def test_valid_semantic_shadow_only_reports_route():
    shadow = Stub(route())
    result = asyncio.run(evaluate_shadow(
        shadow, "fake", "请计算 19+23", has_plan=True, pending_action=None))
    assert result["state"] == "valid"
    assert result["route"] == "agent_loop"
    assert result["host_would_deny"] == "tool_arguments_unverified"
    assert shadow.calls == 1


def test_missing_checkpoint_host_would_deny_without_execution():
    d = route("plan_resume", route_target="saved_plan",
              execution_scope="remaining_steps")
    shadow = Stub(d)
    result = asyncio.run(evaluate_shadow(
        shadow, "fake", "请计算 19+23", has_plan=False, pending_action=None))
    assert result["host_would_deny"] == "missing_saved_plan"


@pytest.mark.parametrize("data", [
    route("respond"),  # invalid respond target/effect/scope
    route(ambiguity="needs_disambiguation"),
    route(source_quote="模型虚构原文"),
])
def test_invalid_or_unbound_proposal_fails_closed(data):
    result = asyncio.run(evaluate_shadow(
        Stub(data), "fake", "请计算 19+23",
        has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"
    assert "route" not in result


def test_provider_failure_is_diagnostic_not_exception():
    result = asyncio.run(evaluate_shadow(
        Stub(error=RuntimeError("no response")), "fake",
        "请计算 19+23", has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"


def test_shadow_schema_rejects_unknown_fields():
    with pytest.raises(ValueError):
        ShadowRoute.model_validate({**route(), "authorized": True})


def test_source_span_verified_and_counted():
    text = "单独计算 19+23，不要执行已保存计划"
    span = {"start": text.index("19+23"), "end": text.index("19+23")+5,
            "exact_text": "19+23"}
    result = asyncio.run(evaluate_shadow(
        Stub(route(source_evidence=[span])),
        "fake", text, has_plan=True, pending_action=None))
    assert result["state"] == "valid"
    assert result["evidence_spans"] == 1
    assert result["host_would_deny"] == "tool_arguments_unverified"
    assert result["host_admission_complete"] is False


def test_forged_span_not_accepted():
    d = route(source_evidence=[{"start": 0, "end": 5, "exact_text": "19+23"}])
    result = asyncio.run(evaluate_shadow(
        Stub(d), "fake", "请计算 19+23", has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"


def test_untrusted_mutation_negative_constraint_rejected():
    text = "不要保存任务"
    data = route(effect_intent="mutation_requested",
                 negative_constraints=[{"start": 0, "end": 2, "exact_text": "不要"}])
    result = asyncio.run(evaluate_shadow(
        Stub(data), "fake", text, has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"


def test_plan_resume_shadow_cannot_claim_full_host_admission():
    d = route("plan_resume", route_target="saved_plan",
              execution_scope="remaining_steps")
    result = asyncio.run(evaluate_shadow(
        Stub(d), "fake", "请计算 19+23", has_plan=True, pending_action=None))
    assert result["host_would_deny"] == "checkpoint_binding_unverified"


def test_clarify_requires_reason_and_question():
    data = route("clarify",route_target="none",execution_scope="none",
                 effect_intent="none")
    assert asyncio.run(evaluate_shadow(
        Stub(data),"fake","请计算 19+23",has_plan=False,pending_action=None))["state"] == "unavailable"
    data["clarification"] = {"required":True, "reason":"missing_argument",
                             "question":"查询哪个词？"}
    assert asyncio.run(evaluate_shadow(
        Stub(data),"fake","请计算 19+23",has_plan=False,pending_action=None))["state"] == "valid"


def test_legacy_comparison_grounded_and_abstains():
    from agent.route_shadow import compare_to_legacy
    shadow = {"state":"valid","route":"plan_create"}
    match = compare_to_legacy(shadow, {
        "status":"partial","termination_reason":"plan_created_pending_execution",
        "trace":[]})
    assert match["comparison"] == "match"
    unknown = compare_to_legacy(shadow, {
        "status":"completed","termination_reason":"model_final",
        "trace":[{"type":"tool","name":"calculate","ok":True}]})
    assert unknown["comparison"] == "mismatch"
    assert unknown["legacy_route_evidence"] == "agent_loop"
    assert unknown["legacy_tool_calls"] == 1
    conflict = compare_to_legacy(shadow, {
        "status":"partial","termination_reason":"plan_revised_pending_execution",
        "trace":[]})
    assert conflict["comparison"] == "mismatch"


def test_plan_execution_scope_is_not_invented_from_trace():
    from agent.route_shadow import compare_to_legacy
    actual = {"status":"completed","trace":[{"type":"plan_resume_execution"}]}
    same = compare_to_legacy({"state":"valid","route":"plan_resume"},actual)
    assert same["comparison"] == "compatible_plan_execution"
    assert same["legacy_route_evidence"] == "saved_plan_execution_unspecified"


def test_legacy_comparison_uses_structured_tool_and_missing_plan_evidence():
    from agent.route_shadow import compare_to_legacy
    tool = compare_to_legacy(
        {"state":"valid","route":"agent_loop"},
        {"status":"completed","termination_reason":"model_final",
         "trace":[{"type":"tool","name":"calculate","ok":True}]})
    assert tool["legacy_route_evidence"] == "agent_loop"
    assert tool["comparison"] == "match"
    absent = compare_to_legacy(
        {"state":"valid","route":"plan_resume"},
        {"status":"partial","termination_reason":"plan_not_found","trace":[]})
    assert absent["legacy_route_evidence"] == "plan_resume"
    assert absent["comparison"] == "match"



def test_validation_diagnostics_contain_only_error_path_and_type():
    bad = route("respond")
    outcome = asyncio.run(evaluate_shadow(
        Stub(bad), "fake", "请计算 19+23",
        has_plan=False, pending_action=None))
    assert outcome["state"] == "unavailable"
    assert outcome["error_class"] == "ValidationError"
    assert outcome["validation_errors"]
    assert set(outcome["validation_errors"][0]) == {"path", "type", "code"}
    assert "请计算" not in json.dumps(outcome, ensure_ascii=False)


def test_shadow_prompt_explicitly_ties_clarification_to_route():
    from agent.route_shadow import PROMPT
    assert '{"required":false,"reason":"none","question":null}' in PROMPT
    assert 'For clarify, set clarification.required=true' in PROMPT
    assert 'HOST denial' in PROMPT


def test_clarification_mismatch_still_rejected_instead_of_repaired():
    bad = route(clarification={
        "required":False,"reason":"missing_argument","question":None})
    outcome = asyncio.run(evaluate_shadow(
        Stub(bad), "fake", "请计算 19+23", has_plan=False, pending_action=None))
    assert outcome["state"] == "unavailable"
    assert outcome["validation_errors"][0]["path"] == "clarification"


def test_shadow_reports_target_without_granting_tool_permission():
    data = route(source_quote="", route_target="pending_clarification",
                 reference={"kind":"pending_slot","pending_field":"term"})
    outcome = asyncio.run(evaluate_shadow(
        Stub(data), "fake", "RAG", has_plan=False,
        pending_action="mcp_reference_lookup"))
    assert outcome["route_target"] == "pending_clarification"
    assert outcome["reference_kind"] == "pending_slot"
    assert outcome["host_admission_complete"] is False


def test_shadow_pending_clarify_requires_host_review():
    data = route("clarify", source_quote="", route_target="pending_clarification",
                 execution_scope="none", effect_intent="none",
                 clarification={"required":True,"reason":"missing_argument",
                                "question":"请确认参数？"})
    result = asyncio.run(evaluate_shadow(
        Stub(data), "fake", "RAG",
        has_plan=False, pending_action="mcp_reference_lookup"))
    assert result["state"] == "valid"
    assert result["host_would_deny"] == "pending_clarification_requires_host_review"


def test_shadow_prompt_explains_single_task_vs_executable_plan():
    from agent.route_shadow import PROMPT
    assert "single task has one title" in PROMPT
    assert "formal executable plan has ordered steps" in PROMPT


def test_prompt_documents_no_step_reference_for_cancel():
    from agent.route_shadow import PROMPT
    assert "Cancel pending argument is not saved_step" in PROMPT
    assert "One-off task-manager item creation is agent_loop/tool_action" in PROMPT


def test_cancel_pending_forged_step_reference_rejected():
    data = route("cancel", source_quote="", route_target="pending_clarification",
                 execution_scope="none", effect_intent="none",
                 reference={"kind":"none","step_number":2})
    result = asyncio.run(evaluate_shadow(
        Stub(data), "fake", "取消这个操作",
        has_plan=False, pending_action="mcp_reference_lookup"))
    assert result["state"] == "unavailable"
    assert result["validation_errors"][0]["path"] == "reference"


def test_value_error_diagnostic_is_bounded_and_source_scoped():
    data = route(source_quote="PRIVATE_SENTINEL_NOT_IN_INPUT")
    result = asyncio.run(evaluate_shadow(
        Stub(data), "fake", "请计算 19+23", has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"
    assert result["failure_stage"] == "source_provenance"
    assert result["validation_code"] == "shadow_untrusted_source_quote"
    assert "PRIVATE_SENTINEL_NOT_IN_INPUT" not in json.dumps(result)


def test_single_task_cannot_pass_plan_step_preview():
    from agent.route_shadow import hypothetical_admission
    span = {"start":3,"end":7,"exact_text":"创建任务"}
    plan = ShadowRoute.model_validate({
        "schema_version":"s25-route-v2","primary_route":"plan_create",
        "route_target":"saved_plan","execution_scope":"none",
        "effect_intent":"mutation_requested","ambiguity":"clear",
        "source_evidence":[span]})
    assert hypothetical_admission(plan,has_plan=False,pending_action=None) == (
        "ordered_plan_steps_evidence_unverified")


def test_real_ordered_plan_requires_two_distinct_grounded_steps():
    from agent.route_shadow import hypothetical_admission, verify_source_spans
    message = "先查询 RAG，再计算 8*8"
    def span(text):
        start=message.index(text)
        return {"start":start,"end":start+len(text),"exact_text":text}
    plan = ShadowRoute.model_validate({
        "schema_version":"s25-route-v2","primary_route":"plan_create",
        "route_target":"saved_plan","execution_scope":"none",
        "effect_intent":"mutation_requested","ambiguity":"clear",
        "source_evidence":[span("先查询 RAG")],
        "plan_step_evidence":[span("查询 RAG"),span("计算 8*8")]})
    verify_source_spans(plan,message)
    assert hypothetical_admission(plan,has_plan=False,pending_action=None) == (
        "write_provenance_unverified")


def test_plan_revision_negative_no_execution_is_not_global_write_conflict():
    from agent.route_shadow import verify_source_spans
    message="修改第二步，不要执行"
    span={"start":message.index("不要执行"),"end":len(message),"exact_text":"不要执行"}
    route=ShadowRoute.model_validate({
        "schema_version":"s25-route-v2","primary_route":"plan_revise",
        "route_target":"saved_plan","execution_scope":"none",
        "effect_intent":"mutation_requested","ambiguity":"clear",
        "negative_constraints":[span]})
    verify_source_spans(route,message)


def test_plan_step_source_offsets_still_fail_closed():
    from agent.route_shadow import verify_source_spans
    route=ShadowRoute.model_validate({
        "schema_version":"s25-route-v2","primary_route":"plan_create",
        "route_target":"saved_plan","execution_scope":"none",
        "effect_intent":"mutation_requested","ambiguity":"clear",
        "plan_step_evidence":[{"start":0,"end":3,"exact_text":"虚构步骤"}]})
    with pytest.raises(ValueError,match="shadow_source_text_not_found"):
        verify_source_spans(route,"创建计划")


def test_pending_reply_needs_original_request_binding_even_if_model_names_field():
    from agent.route_shadow import hypothetical_admission
    route = ShadowRoute.model_validate({
        "schema_version":"s25-route-v2","primary_route":"agent_loop",
        "route_target":"pending_clarification","execution_scope":"single_tool",
        "effect_intent":"read_only","ambiguity":"clear",
        "reference":{"kind":"pending_slot","pending_field":"term"}})
    assert hypothetical_admission(route,has_plan=False,pending_action="mcp_reference_lookup") == (
        "pending_original_request_binding_unverified")
    assert hypothetical_admission(route,has_plan=False,pending_action=None) == (
        "missing_pending_action")


def test_source_span_error_types_are_distinguished():
    from agent.route_shadow import verify_source_spans
    message = "取消此前操作"
    base = {
        "schema_version":"s25-route-v2","primary_route":"cancel",
        "route_target":"none","execution_scope":"none",
        "effect_intent":"none","ambiguity":"clear"}
    cases = [
        ({"start":0,"end":100,"exact_text":"取消"}, "shadow_source_offset_out_of_bounds"),
        ({"start":1,"end":3,"exact_text":"取消"}, "shadow_source_offset_mismatch"),
        ({"start":0,"end":2,"exact_text":"不存在"}, "shadow_source_text_not_found"),
    ]
    for span, expected in cases:
        proposal = ShadowRoute.model_validate({**base, "source_evidence":[span]})
        with pytest.raises(ValueError, match=expected):
            verify_source_spans(proposal, message)


def test_source_span_error_includes_only_group_and_index():
    text = "取消此前操作"
    bad = route("cancel", source_quote="", route_target="pending_clarification",
                execution_scope="none", effect_intent="none",
                negative_constraints=[
                    {"start":0,"end":100,"exact_text":"请不要执行"}])
    outcome = asyncio.run(evaluate_shadow(
        Stub(bad), "fake", text, has_plan=False, pending_action="reference"))
    assert outcome["validation_code"] == "shadow_source_offset_out_of_bounds"
    assert outcome["source_group"] == "negative_constraints"
    assert outcome["source_index"] == 0
    assert "请不要执行" not in json.dumps(outcome, ensure_ascii=False)


def test_prompt_payload_supplies_exact_unicode_index_guide():
    class CapturingStub(Stub):
        async def complete(self, **kwargs):
            self.messages = kwargs["messages"]
            return await super().complete(**kwargs)

    stub = CapturingStub(route(source_quote=""))
    asyncio.run(evaluate_shadow(stub, "fake", "取消操作",
                                has_plan=False, pending_action=None))
    payload = json.loads(stub.messages[1]["content"])
    assert payload["index_guide"] == [[0,"取"],[1,"消"],[2,"操"],[3,"作"]]
    assert payload["pending_action_field"] is None


def test_evaluate_shadow_can_apply_complete_readonly_host_fixture():
    from agent.route_shadow_host import ShadowHostFacts
    outcome = asyncio.run(evaluate_shadow(
        Stub(route(source_quote="")), "fake", "计算 19+23",
        has_plan=False, pending_action=None,
        host_facts=ShadowHostFacts(
            has_plan=False,current_read_bound=True,tool_registered=True,
            tool_schema_valid=True,source_provenance_valid=True)))
    assert outcome["host_preview"]["admitted_route"] == "ordinary_loop"
    assert outcome["host_preview"]["evidence_complete"] is True


def test_confusion_matrix_handles_unasserted_optional_field():
    from evals.s25_02_m2_shadow_live import confusion
    result = confusion([
        {"expected_effect":None,"proposed_effect":"none"},
        {"expected_effect":"read_only","proposed_effect":"read_only"},
    ], "expected_effect", "proposed_effect")
    assert {row["expected"] for row in result} == {"not_asserted", "read_only"}
