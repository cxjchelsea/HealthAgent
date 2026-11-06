"""Bounded primary-route-v1 HTTP canary.

No Legacy semantic reviewer is invoked here. The canary is deliberately
non-default: plan CREATE/CANCEL reuse proven Host admission and durable Turn
finalization; the remaining routes fail closed until WP2/WP3 admission exists.
A model proposal is never its own authorization.
"""
from agent.m4_guarded_cancel import admit_guarded_plan_cancel
from agent.m4_guarded_plan import admit_guarded_plan_create
from agent.plan_intent import PlanRequest, build_plan_checkpoint
from agent.plan_revision_review import RevisionProposal, apply_plan_revision
from agent.planning import PlanLedger
from agent.primary_route_v1 import BoundPrimaryProposal


def admit_v1_canary(proposal: BoundPrimaryProposal, *, message: str,
                    conversation_id: str, request_id: str, turn_id: str,
                    checkpoint: dict | None) -> dict:
    kind = proposal.route_kind
    trace = [{"step": 0, "type": "primary_route_v1",
              "schema_version": proposal.schema_version, "route_kind": kind}]

    def deny(reason: str, answer: str) -> dict:
        return {"run_id": turn_id, "status": "partial",
                "termination_reason": reason, "answer": answer,
                "trace": trace, "steps": 0}

    if kind == "plan_create":
        if checkpoint is not None:
            return deny("primary_v1_plan_exists", "已有保存计划，请明确修改现有计划。")
        steps = [{"goal": s.goal, "expected_tool": s.tool_name,
                  "expected_arguments": s.arguments} for s in proposal.steps]
        try:
            new_checkpoint = build_plan_checkpoint(
                message, PlanRequest(intent="plan_only", steps=steps))
        except (ValueError, TypeError):
            return deny("primary_v1_create_binding_denied",
                        "计划步骤未通过可信参数验证，未保存计划。")
        if not admit_guarded_plan_create(
                message=message, conversation_id=conversation_id,
                request_id=request_id, checkpoint=new_checkpoint,
                turn_created=True, semantic_confirmed=True):
            return deny("primary_v1_create_admission_denied",
                        "计划未通过受保护的建立准入，未保存计划。")
        return {"run_id": turn_id, "status": "partial",
                "termination_reason": "plan_created_pending_execution",
                "answer": "已建立计划，尚未执行任何步骤。请指定执行步骤。",
                "trace": trace + [{"step": 0, "type": "plan_created"}],
                "steps": 0, "plan": new_checkpoint["steps"],
                "plan_revision": new_checkpoint["revision"],
                "plan_events": [{"event": "plan_created",
                                 "revision": new_checkpoint["revision"]}],
                "plan_checkpoint": new_checkpoint}

    if kind == "plan_revise":
        if checkpoint is None:
            return deny("primary_v1_missing_plan", "没有可修改的已保存计划。")
        try:
            snapshot = PlanLedger.restore(checkpoint).snapshot()
            n = proposal.step_number
            if type(n) is not int or not 1 <= n <= len(snapshot):
                return deny("primary_v1_revision_unbound", "修改步骤没有通过绑定验证。")
            old, new = proposal.revision_old_argument, proposal.revision_new_argument
            if not old or not new:
                return deny("primary_v1_revision_unbound", "请明确原表达式和修改后的表达式。")
            revision = apply_plan_revision(checkpoint, message, RevisionProposal(
                intent="revise", edit_requested=True, step_number=n,
                expected_tool="calculate", old_argument=old,
                new_argument=new))
        except (ValueError, TypeError, KeyError):
            revision = None
        if revision is None:
            return deny("plan_revision_conflict",
                        "计划修改未通过步骤和原参数绑定校验；原计划保持不变。")
        updated = revision["checkpoint"]
        # Final durable plan CAS occurs in the Turn commit in apps/api/main.py.
        return {"run_id": turn_id, "status": "partial",
                "termination_reason": "plan_revised_pending_execution",
                "answer": "已更新计划指定步骤，其余步骤保持不变；未执行工具。",
                "trace": trace + [{"step": 0, "type": "plan_revised"}],
                "steps": 0, "plan": updated["steps"],
                "plan_checkpoint": updated,
                "plan_revision": updated["revision"],
                "plan_events": [revision["event"]]}

    if kind == "plan_cancel":
        if checkpoint is None:
            return deny("primary_v1_missing_plan", "没有可取消的已保存计划。")
        if not admit_guarded_plan_cancel(
                message=message, conversation_id=conversation_id,
                request_id=request_id, checkpoint=checkpoint,
                turn_created=True, semantic_confirmed=True,
                primary_bound=True):
            return deny("primary_v1_cancel_admission_denied",
                        "无法验证计划取消权限，未取消计划。")
        return {"run_id": turn_id, "status": "completed",
                "termination_reason": "plan_cancelled",
                "answer": "已取消当前保存的计划，未执行任何步骤。",
                "trace": trace + [{"step": 0, "type": "plan_cancelled"}],
                "steps": 0, "plan": [],
                "plan_revision": checkpoint["revision"],
                "plan_events": [{"event": "plan_cancelled",
                                 "revision": checkpoint["revision"]}],
                "plan_cancellation_requested": True}

    if kind == "clarify" and proposal.pending_write_tool_name == "create_task":
        if not proposal.clarification_question:
            return deny("pending_write_question_missing", "请补充任务名称。")
        return {"run_id": turn_id, "status": "partial",
                "termination_reason": "needs_input",
                "pending_action": "create_task",
                "pending_write_authority_candidate": True,
                "answer": proposal.clarification_question,
                "trace": trace + [{"step": 0, "type": "pending_write_clarification"}],
                "steps": 0}

    if kind == "clarify":
        # Clarification is informational only. No implicit pending tool
        # authorization, executable action, or checkpoint write.
        if not proposal.clarification_question:
            return deny("primary_v1_clarification_unbound",
                        "请补充请求所需的信息。")
        return {"run_id": turn_id, "status": "partial",
                "termination_reason": ("needs_input" if proposal.pending_read_tool_name else "primary_v1_clarification_required"),
                "pending_action": proposal.pending_read_tool_name,
                "answer": proposal.clarification_question,
                "trace": trace + [{"step": 0, "type": "clarification_requested"}],
                "steps": 0}

    if kind == "reject":
        return deny("primary_v1_route_rejected",
                    "本轮请求无法安全执行或识别，请重新说明目标。")

    if kind == "plan_missing":
        return deny("plan_not_found", "没有可继续执行的已保存计划。")

    # These are intentionally not silently routed to Legacy or AgentLoop.
    # They will be enabled only after independent deterministic Host admission
    # and shared execution governance are wired in WP2/WP3.
    return deny("primary_v1_admission_not_wired",
                "当前 v1 试运行路径尚未授权此类操作，未执行工具或修改计划。")
