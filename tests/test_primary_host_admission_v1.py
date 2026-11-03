"""Host-only v1 admission contract: no semantic call, tool dispatch or mutation."""
import pytest

from agent.plan_intent import PlanRequest, build_plan_checkpoint
from agent.primary_host_admission_v1 import (
    PrimaryHostFacts, admit_primary_route_v1,
)
from agent.primary_route_v1 import RouteSignal, bind_route_signal


CREATE = "请创建两步计划：第一步计算3+4，第二步计算8+9，只保存不执行"
CHECKPOINT = build_plan_checkpoint(CREATE, PlanRequest(intent="plan_only", steps=[
    {"goal": "计算3+4", "expected_tool": "calculate", "expected_arguments": {"expression": "3+4"}},
    {"goal": "计算8+9", "expected_tool": "calculate", "expected_arguments": {"expression": "8+9"}},
]))


def bound(kind, message, quote, *, checkpoint=None, effect="none",
          scope="none", step_number=None, pending=None, verified=False,
          old=None, new=None, steps=None):
    body = {
        "schema_version": "primary-route-v1",
        "route_kind": kind, "quote": quote, "effect_intent": effect,
        "execution_scope": scope, "steps": steps or [],
        "step_number": step_number,
        "revision_old_argument": old, "revision_new_argument": new}
    if kind == "clarify":
        body["clarification_question"] = "请提供参数"
    p = RouteSignal.model_validate(body)
    return bind_route_signal(p, message, saved_plan_present=checkpoint is not None,
                             pending_action=pending, pending_origin_verified=verified,
                             saved_step_count=len(checkpoint["steps"]) if checkpoint else None)


def facts(checkpoint=None, pending=None, verified=False, guarded=True, created=True):
    return PrimaryHostFacts(checkpoint=checkpoint, pending_action=pending,
                            pending_origin_verified=verified,
                            guarded_mode=guarded, turn_created=created)


@pytest.mark.parametrize("kind,effect,mode", [
    ("ordinary_tool", "read_only", "read_only"),
    ("respond", "none", "respond_only"),
])
def test_host_admits_nonmutating_routes_without_reclassification(kind, effect, mode):
    p = bound(kind, "请计算3+4", "计算3+4", effect=effect)
    d = admit_primary_route_v1(p, facts())
    assert (d.route_kind, d.disposition, d.execution_mode) == (kind, "allow", mode)


@pytest.mark.parametrize("guarded,created", [(False, True), (True, False)])
def test_host_rejects_untrusted_preconditions(guarded, created):
    p = bound("ordinary_tool", "请计算3+4", "计算3+4", effect="read_only")
    assert admit_primary_route_v1(p, facts(guarded=guarded, created=created)).disposition == "deny"


def test_bound_create_task_requires_proposal_and_host_admission():
    message = "创建任务：提交测试报告"
    p = bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool", "quote": message,
        "effect_intent": "mutation_requested", "execution_scope": "none",
        "write_tool_name": "create_task",
        "write_arguments": {"title": "提交测试报告"},
    }), message, saved_plan_present=False, pending_action=None)
    d = admit_primary_route_v1(p, PrimaryHostFacts(
        checkpoint=None, pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True, write_consent=False))
    assert d.disposition == "allow"
    assert d.reason == "primary_v1_create_task_candidate"
    blocked = admit_primary_route_v1(p, PrimaryHostFacts(
        checkpoint=None, pending_action="create_task", pending_origin_verified=True,
        guarded_mode=True, turn_created=True))
    assert blocked.disposition == "restrict"


@pytest.mark.parametrize("kind,scope,control", [
    ("plan_execute_first", "first_step", "execute_first"),
    ("plan_resume", "remaining_steps", "resume"),
])
def test_host_routes_valid_plan_to_existing_guards(kind, scope, control):
    p = bound(kind, "执行已保存计划的步骤", "已保存计划",
              checkpoint=CHECKPOINT, effect="read_only", scope=scope)
    d = admit_primary_route_v1(p, facts(CHECKPOINT))
    assert (d.disposition, d.execution_mode, d.plan_control) == ("allow", "read_only", control)


def test_host_cannot_expand_resume_to_all_steps():
    p = bound("plan_resume", "执行全部已保存计划", "全部已保存计划",
              checkpoint=CHECKPOINT, effect="read_only", scope="all_steps")
    assert admit_primary_route_v1(p, facts(CHECKPOINT)).reason == "primary_v1_scope_not_bounded"


def test_host_refuses_completed_first_step():
    cp = {"revision": 1, "next_step_id": 3, "steps": [
        {**CHECKPOINT["steps"][0], "status": "completed", "evidence_tool": "calculate"},
        CHECKPOINT["steps"][1]]}
    p = bound("plan_execute_first", "只执行第一步", "第一步",
              checkpoint=cp, effect="read_only", scope="first_step")
    assert admit_primary_route_v1(p, facts(cp)).reason == "primary_v1_no_pending_steps"


def test_host_refuses_untrusted_plan_binding():
    cp = {"revision": 1, "next_step_id": 3, "steps": [
        {**CHECKPOINT["steps"][0], "expected_tool": "create_task",
         "expected_arguments": {"title": "unexpected"}},
        CHECKPOINT["steps"][1]]}
    p = bound("plan_resume", "继续执行剩余步骤", "剩余步骤",
              checkpoint=cp, effect="read_only", scope="remaining_steps")
    assert admit_primary_route_v1(p, facts(cp)).disposition == "deny"


def test_revision_must_target_pending_step():
    cp = {"revision": 1, "next_step_id": 3, "steps": [
        {**CHECKPOINT["steps"][0], "status": "completed", "evidence_tool": "calculate"},
        CHECKPOINT["steps"][1]]}
    p = bound("plan_revise", "修改第一步，由3+4改为7+8", "修改第一步",
              checkpoint=cp, effect="mutation_requested", step_number=1,
              old="3+4", new="7+8")
    assert admit_primary_route_v1(p, facts(cp)).reason == "primary_v1_revision_step_unavailable"


def test_pending_readonly_admits_only_verified_origin():
    p = bound("clarify_response", "MCP", "MCP", pending="mcp_reference_lookup",
              verified=True)
    d = admit_primary_route_v1(p, facts(
        pending="mcp_reference_lookup", verified=True))
    assert d.disposition == "allow"
    assert d.reason == "primary_v1_pending_read_only"


def test_response_scope_cannot_be_rewritten_by_host():
    p = bound("clarify", "请帮助处理", "帮助处理")
    d = admit_primary_route_v1(p, facts())
    assert d.disposition == "clarify"
    assert d.route_kind == p.route_kind



def test_host_revision_requires_exact_trusted_old_argument_before_admission():
    message = "把第二步由计算8+9改成计算10+11，第一步不变"
    exact = bound("plan_revise", message, "第二步由计算8+9改成计算10+11",
                  checkpoint=CHECKPOINT, effect="mutation_requested",
                  step_number=2, old="8+9", new="10+11")
    approved = admit_primary_route_v1(exact, facts(CHECKPOINT))
    assert approved.reason == "primary_v1_revision_candidate"
    assert approved.disposition == "allow"

    # Prefix is verbatim in user wording, but is NOT the stored tool argument.
    # Host must not convert or trim it to authorize the change.
    prefixed = bound("plan_revise", message, "第二步由计算8+9改成计算10+11",
                     checkpoint=CHECKPOINT, effect="mutation_requested",
                     step_number=2, old="计算8+9", new="10+11")
    denied = admit_primary_route_v1(prefixed, facts(CHECKPOINT))
    assert denied.reason == "primary_v1_revision_old_argument_mismatch"
    assert denied.disposition == "deny"
    assert CHECKPOINT["steps"][1]["expected_arguments"] == {"expression": "8+9"}
