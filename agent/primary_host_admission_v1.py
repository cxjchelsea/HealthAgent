"""Deterministic Host admission for primary-route-v1.

Semantic intent remains a proposal. This admission may deny/restrict a proposal
but cannot rewrite route_kind, infer arguments, dispatch tools or mutate state.
Final M4 claim, permission, dispatch journal and Turn CAS are separate guards.
"""
from dataclasses import dataclass
from typing import Literal

from agent.primary_route_v1 import BoundPrimaryProposal
from agent.pending_write_withdrawal_guard import explicitly_withdraws_pending_task_write
from agent.plan_intent import READ_TOOLS
from agent.planning import PlanLedger
from agent.tools import SCHEMAS


@dataclass(frozen=True)
class PrimaryHostFacts:
    checkpoint: dict | None
    pending_action: str | None
    pending_origin_verified: bool
    guarded_mode: bool
    turn_created: bool
    write_consent: bool = False
    pending_write_authority_verified: bool = False
    current_request: str = ""


@dataclass(frozen=True)
class PrimaryHostDecision:
    disposition: Literal["allow", "deny", "clarify", "restrict"]
    reason: str
    route_kind: str
    execution_mode: Literal["none", "respond_only", "read_only"] = "none"
    plan_control: Literal["execute_first", "resume"] | None = None


def admit_primary_route_v1(
        proposal: BoundPrimaryProposal, facts: PrimaryHostFacts,
        ) -> PrimaryHostDecision:
    """A single deterministic decision; no model calls and no side effects."""
    kind = proposal.route_kind

    def decide(disposition, reason, mode="none", control=None):
        return PrimaryHostDecision(disposition, reason, kind, mode, control)

    if not facts.turn_created or not facts.guarded_mode:
        return decide("deny", "primary_v1_host_precondition_denied")

    if kind == "clarify_response":
        if not (facts.pending_action and facts.pending_origin_verified):
            return decide("deny", "primary_v1_pending_origin_unverified")
        # Only the known read-only MCP lookup can use the existing
        # prepared/receipt evidence store without write-side idempotency.
        # create_task remains restricted until write authority is durable.
        if facts.pending_action == "mcp_reference_lookup":
            return decide("allow", "primary_v1_pending_read_only", "read_only")
        if facts.pending_action == "create_task":
            if explicitly_withdraws_pending_task_write(facts.current_request):
                return decide("restrict", "primary_v1_pending_task_withdrawn")
            if facts.pending_write_authority_verified:
                return decide("allow", "primary_v1_pending_task_authorized")
        return decide("restrict", "primary_v1_pending_write_not_governed")

    if kind == "plan_missing":
        if facts.checkpoint is not None:
            return decide("deny", "primary_v1_plan_exists")
        return decide("restrict", "primary_v1_missing_plan_status")

    if kind == "reject":
        return decide("restrict", "primary_v1_route_rejected")

    if kind == "clarify":
        return decide("clarify", "primary_v1_clarification_required")

    if kind == "respond":
        return decide("allow", "primary_v1_answer_only", "respond_only")

    if kind == "ordinary_tool":
        if proposal.effect_intent == "mutation_requested":
            # Pending writes require independent durable prior authorization;
            # do not reinterpret a pending answer as a new write.
            if facts.pending_action is not None:
                return decide("restrict", "primary_v1_pending_write_not_governed")
            fields = {"create_task": {"title"}, "save_memory": {"key", "value"}}
            if (proposal.write_tool_name not in fields
                    or not proposal.write_arguments
                    or set(proposal.write_arguments) != fields[proposal.write_tool_name]):
                return decide("restrict", "primary_v1_admission_not_wired")
            if proposal.write_tool_name == "save_memory":
                if not facts.write_consent:
                    return decide("deny", "primary_v1_memory_consent_required")
                return decide("allow", "primary_v1_save_memory_candidate")
            return decide("allow", "primary_v1_create_task_candidate")
        if proposal.effect_intent != "read_only":
            return decide("deny", "primary_v1_ordinary_effect_invalid")
        return decide("allow", "primary_v1_ordinary_read_only", "read_only")

    if kind == "plan_create":
        if facts.checkpoint is not None:
            return decide("deny", "primary_v1_plan_exists")
        if not 2 <= len(proposal.steps) <= 8:
            return decide("deny", "primary_v1_create_steps_invalid")
        return decide("allow", "primary_v1_create_candidate")

    if kind in {"plan_cancel", "plan_revise", "plan_execute_first", "plan_resume"}:
        if facts.checkpoint is None:
            return decide("deny", "primary_v1_missing_plan")
        try:
            ledger = PlanLedger.restore(facts.checkpoint)
            steps = ledger.snapshot()
        except (ValueError, TypeError, KeyError):
            return decide("deny", "primary_v1_checkpoint_invalid")
        if kind == "plan_revise":
            n = proposal.step_number
            if (type(n) is not int or n < 1 or n > len(steps)
                    or steps[n - 1]["status"] != "pending"):
                return decide("deny", "primary_v1_revision_step_unavailable")
            # A semantic proposal never grants permission to replace an
            # arbitrary existing argument. Bind it to trusted Host snapshot
            # before accepting a candidate. A mismatch remains fail-closed;
            # do not strip prefixes or rewrite the model's claimed argument.
            target = steps[n - 1]
            if (target.get("expected_tool") != "calculate"
                    or not isinstance(target.get("expected_arguments"), dict)
                    or target["expected_arguments"].get("expression")
                    != proposal.revision_old_argument):
                return decide("deny", "primary_v1_revision_old_argument_mismatch")
            return decide("allow", "primary_v1_revision_candidate")
        if kind == "plan_cancel":
            return decide("allow", "primary_v1_cancel_candidate")
        scope = "execute_first" if kind == "plan_execute_first" else "resume"
        if kind == "plan_resume" and proposal.execution_scope != "remaining_steps":
            return decide("deny", "primary_v1_scope_not_bounded")
        eligible = [steps[0]] if scope == "execute_first" else [
            s for s in steps if s["status"] == "pending"]
        if not eligible or any(s["status"] != "pending" for s in eligible):
            return decide("deny", "primary_v1_no_pending_steps")
        for step in eligible:
            name, arguments = step.get("expected_tool"), step.get("expected_arguments")
            if name not in READ_TOOLS or name not in SCHEMAS or not isinstance(arguments, dict):
                return decide("deny", "primary_v1_step_untrusted")
            try:
                if SCHEMAS[name].model_validate(arguments).model_dump() != arguments:
                    return decide("deny", "primary_v1_step_untrusted")
            except ValueError:
                return decide("deny", "primary_v1_step_untrusted")
        return decide("allow", "primary_v1_plan_execution_candidate",
                      "read_only", scope)

    return decide("deny", "primary_v1_route_unknown")
