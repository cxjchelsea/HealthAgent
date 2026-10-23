"""S25-02 M2: side-effect-free V2 semantic shadow, never Host execution authority."""
import json
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    exact_text: str = Field(min_length=1, max_length=400)


class ShadowReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["none", "active_goal", "saved_plan", "saved_step", "pending_slot"] = "none"
    step_number: int | None = Field(default=None, ge=1, le=8)
    pending_field: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def bound(self):
        if (self.kind == "saved_step") != (self.step_number is not None):
            raise ValueError("shadow_step_reference_mismatch")
        if self.kind != "pending_slot" and self.pending_field is not None:
            raise ValueError("shadow_pending_reference_mismatch")
        return self


class ShadowClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: bool = False
    reason: Literal["none", "missing_referent", "missing_argument",
                    "conflicting_constraints", "ambiguous_scope", "uncertain_intent"] = "none"
    question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def consistent(self):
        if self.required != (self.reason != "none"):
            raise ValueError("shadow_clarification_mismatch")
        if self.required and not self.question:
            raise ValueError("shadow_missing_question")
        return self

from agent.understanding import GoalSpec, GoalChangeProposal, HTTPUnderstandingModel


class ShadowRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["s25-route-v2"]
    primary_route: Literal["respond", "clarify", "agent_loop", "plan_create",
                           "plan_execute_first", "plan_resume", "plan_revise", "cancel"]
    route_target: Literal["none", "active_goal", "saved_plan",
                          "pending_clarification", "tool_action"]
    execution_scope: Literal["none", "single_tool", "first_step",
                             "remaining_steps", "all_steps"]
    effect_intent: Literal["none", "read_only", "mutation_requested"]
    ambiguity: Literal["clear", "needs_disambiguation", "unreliable"]
    source_quote: str = Field(max_length=400, default="")
    source_evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=12)
    negative_constraints: list[EvidenceSpan] = Field(default_factory=list, max_length=8)
    reference: ShadowReference = Field(default_factory=ShadowReference)
    clarification: ShadowClarification = Field(default_factory=ShadowClarification)
    goal: GoalSpec | None = None
    goal_change: GoalChangeProposal | None = None

    @model_validator(mode="after")
    def combinations(self):
        routes = {
            "respond": ({"none", "active_goal"}, {"none"}, {"none"}),
            "clarify": ({"none","active_goal","saved_plan",
                         "pending_clarification","tool_action"}, {"none"}, {"none"}),
            "agent_loop": ({"none","tool_action","pending_clarification"},
                           {"none","single_tool"}, {"none","read_only","mutation_requested"}),
            "plan_create": ({"none","saved_plan"}, {"none"}, {"mutation_requested"}),
            "plan_execute_first": ({"saved_plan"}, {"first_step"},
                                   {"read_only","mutation_requested"}),
            "plan_resume": ({"saved_plan"}, {"remaining_steps","all_steps"},
                            {"read_only","mutation_requested"}),
            "plan_revise": ({"saved_plan"}, {"none"}, {"mutation_requested"}),
            "cancel": ({"none","active_goal","saved_plan","pending_clarification"},
                       {"none"}, {"none"}),
        }
        targets, scopes, effects = routes[self.primary_route]
        if (self.route_target not in targets or self.execution_scope not in scopes
                or self.effect_intent not in effects):
            raise ValueError("invalid_shadow_route_combination")
        if self.ambiguity != "clear" and self.primary_route not in {"clarify","respond"}:
            raise ValueError("ambiguous_effect_route")
        if self.primary_route == "clarify" and not self.clarification.required:
            raise ValueError("shadow_clarify_requires_question")
        if self.primary_route != "clarify" and self.clarification.required:
            raise ValueError("shadow_nonclarify_has_question")
        if self.reference.kind == "saved_step" and self.primary_route not in {
                "plan_execute_first", "plan_revise"}:
            raise ValueError("shadow_step_wrong_route")
        if self.reference.kind == "pending_slot" and self.route_target != "pending_clarification":
            raise ValueError("shadow_pending_wrong_target")
        return self


def verify_source_spans(route: ShadowRoute, message: str) -> None:
    if route.source_quote and route.source_quote not in message:
        raise ValueError("shadow_untrusted_source_quote")
    for span in [*route.source_evidence, *route.negative_constraints]:
        if (span.end > len(message) or span.end <= span.start
                or message[span.start:span.end] != span.exact_text):
            raise ValueError("shadow_untrusted_source_span")
    # An ungrounded negative constraint cannot silently be discarded by admission.
    if route.negative_constraints and route.effect_intent == "mutation_requested":
        raise ValueError("shadow_mutation_conflicts_negative_constraint")


def hypothetical_admission(route: ShadowRoute, *, has_plan: bool,
                           pending_action: str | None) -> str | None:
    if route.ambiguity != "clear":
        return "ambiguous_route"
    if route.primary_route in {"plan_execute_first", "plan_resume", "plan_revise"} and not has_plan:
        return "missing_saved_plan"
    if route.primary_route == "plan_create" and has_plan:
        return "existing_saved_plan"
    if route.route_target == "pending_clarification" and pending_action is None:
        return "missing_pending_action"
    if route.reference.kind == "pending_slot" and (pending_action is None or
            route.reference.pending_field is None):
        return "pending_binding_unverified"
    if route.primary_route in {"plan_execute_first", "plan_resume", "plan_revise"}:
        return "checkpoint_binding_unverified"
    if route.effect_intent == "mutation_requested":
        return "write_provenance_unverified"
    if route.primary_route == "agent_loop" and route.effect_intent == "read_only":
        return "tool_arguments_unverified"
    return None


PROMPT = """Return exactly JSON matching the provided schema. Identify ONE current-turn
semantic route proposal, not authorization. Existing saved plans and pending
clarifications are context only. An independent tool request must not resume a
saved plan. A written draft is NOT a persistent plan. Do not infer permission
to write from history. If ambiguous select clarify. Do not invent plan/step
authority, tool availability, completed steps or observations. source_quote
must be exact text from CURRENT user message (or empty if unavailable).
Never execute tools. This is a diagnostic shadow-only classification.\nOnly include source_evidence offsets verified against the current user message;\nuse empty arrays if uncertain. Explicit negative constraints must be quoted with\nexact offsets. A clarification route requires reason and question."""


async def evaluate_shadow(provider: HTTPUnderstandingModel, model_name: str,
                          message: str, *, has_plan: bool,
                          pending_action: str | None, timeout_s: float = 12) -> dict:
    """Isolated evaluation: never invokes AgentLoop, tools, stores or permissions."""
    import asyncio
    began = time.perf_counter()
    try:
        raw = await asyncio.wait_for(provider.complete(
            model=model_name, response_schema=ShadowRoute.model_json_schema(),
            messages=[{"role":"system","content":PROMPT},
                      {"role":"user","content":json.dumps({
                          "message":message,"has_saved_plan":has_plan,
                          "has_pending_action":pending_action is not None
                      },ensure_ascii=False)}]), timeout=timeout_s)
        result = ShadowRoute.model_validate_json(raw)
        verify_source_spans(result, message)
        would_deny = hypothetical_admission(
            result, has_plan=has_plan, pending_action=pending_action)
        return {"state":"valid","route":result.primary_route,
                "scope":result.execution_scope,"effect":result.effect_intent,
                "ambiguity":result.ambiguity,"host_would_deny":would_deny,\n                "host_admission_complete":False,\n                "evidence_spans":len(result.source_evidence),
                "elapsed_ms":int((time.perf_counter()-began)*1000)}
    except Exception as exc:
        return {"state":"unavailable","error_class":type(exc).__name__,
                "elapsed_ms":int((time.perf_counter()-began)*1000)}


def compare_to_legacy(shadow: dict, result: dict) -> dict:
    """Only infer known legacy routes from trusted Host outcome markers.

    Never use answer wording to infer execution and never claim equivalence
    where a terminal reason cannot uniquely identify the admitted route.
    """
    reasons = {
        "plan_created_pending_execution": "plan_create",
        "plan_revised_pending_execution": "plan_revise",
        "plan_first_step_already_completed": "plan_execute_first",
        "plan_first_step_verified": "plan_execute_first",
        "needs_input": "clarify",
        "unsupported_capability": "reject",
    }
    reason = result.get("termination_reason")
    observed = reasons.get(reason)
    trace = result.get("trace", [])
    if any(event.get("type") == "plan_resume_execution" for event in trace):
        # Bounded saved-plan execution is evidence of a plan route, but without
        # the original Host scope we cannot distinguish execute_first/resume.
        observed = "saved_plan_execution_unspecified"
    outcome = "uncomparable"
    if shadow.get("state") == "valid" and observed is not None:
        if observed == "saved_plan_execution_unspecified":
            outcome = ("compatible_plan_execution" if shadow.get("route")
                       in {"plan_execute_first", "plan_resume"} else "mismatch")
        elif observed == "reject":
            outcome = "not_comparable_to_semantic_route"
        else:
            outcome = "match" if shadow.get("route") == observed else "mismatch"
    return {"legacy_route_evidence": observed or "unknown",
            "comparison": outcome,
            "legacy_status": result.get("status"),
            "legacy_termination_reason": reason,
            "legacy_tool_calls": sum(
                event.get("type") == "tool" for event in trace)}
