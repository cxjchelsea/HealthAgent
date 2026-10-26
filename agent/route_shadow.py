"""S25-02 M2: side-effect-free V2 semantic shadow, never Host execution authority."""
import json
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from agent.understanding import GoalSpec, GoalChangeProposal, HTTPUnderstandingModel


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
    plan_step_evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=8)
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
    for span in [*route.source_evidence, *route.negative_constraints,
                 *route.plan_step_evidence]:
        if span.end > len(message) or span.end <= span.start:
            raise ValueError("shadow_source_offset_out_of_bounds")
        if message[span.start:span.end] != span.exact_text:
            if span.exact_text in message:
                raise ValueError("shadow_source_offset_mismatch")
            raise ValueError("shadow_source_text_not_found")
    # Source spans are exact character offsets; invalid evidence is never repaired.
    # A negative constraint is scoped to its object. "Revise plan, do not
    # execute" does not prohibit the revision itself. Host resolves scope.


def hypothetical_admission(route: ShadowRoute, *, has_plan: bool,
                           pending_action: str | None) -> str | None:
    if route.ambiguity != "clear":
        return "ambiguous_route"
    if route.primary_route in {"plan_execute_first", "plan_resume", "plan_revise"} and not has_plan:
        return "missing_saved_plan"
    if route.primary_route == "plan_create" and has_plan:
        return "existing_saved_plan"
    if route.primary_route == "plan_create":
        if not route.source_evidence:
            return "plan_intent_evidence_unverified"
        spans = route.plan_step_evidence
        if len(spans) < 2 or len({(x.start, x.end) for x in spans}) < 2:
            return "ordered_plan_steps_evidence_unverified"
    if route.route_target == "pending_clarification" and pending_action is None:
        return "missing_pending_action"
    if route.primary_route == "clarify" and route.route_target == "pending_clarification":
        # A model may request another question, but a bound reply is not
        # authority to re-open an already answered pending tool slot.
        return "pending_clarification_requires_host_review"
    if route.route_target == "pending_clarification" and route.primary_route == "agent_loop":
        if pending_action is None:
            return "missing_pending_action"
        if route.reference.kind != "pending_slot":
            return "pending_reference_unverified"
        if route.reference.pending_field is None:
            return "pending_field_unverified"
        return "pending_original_request_binding_unverified"
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
Never execute tools. This is a diagnostic shadow-only classification.\nOnly include source_evidence offsets verified against the current user message;\nuse empty arrays if uncertain. Explicit negative constraints must be quoted with\nexact offsets. A clarification route requires reason and question.
IMPORTANT clarification JSON rules (for EVERY route):
For all routes OTHER THAN clarify, set clarification exactly to
{"required":false,"reason":"none","question":null}.
For clarify, set clarification.required=true, reason to one of
missing_referent/missing_argument/conflicting_constraints/ambiguous_scope/
uncertain_intent (NEVER "none"), and supply a nonempty question.
Never provide a non-none clarification reason when required=false.
For a clear request set ambiguity="clear". For uncertain requests choose
primary_route="clarify" and provide a matching clarification object.
Route combinations: respond/cancel => scope none and effect none;
agent_loop => scope single_tool or none; plan_create/plan_revise => scope none,
effect mutation_requested; plan_execute_first => scope first_step;
plan_resume => scope remaining_steps or all_steps. No other combination.
If there is NO saved plan, a reference to continuing one is still a
plan_resume semantic proposal: lack of checkpoint is a HOST denial, not an
excuse to invent steps.
Routing distinctions (semantic proposals ONLY, never authorizations):
- Decide between plan_create and agent_loop from the REQUESTED PERSISTED
  OBJECT and its lifecycle. A single task has one title and is immediately
  created by the task tool. A formal executable plan has ordered steps and
  is stored for later stepwise execution. Both can contain the word "task".
- Answering an existing missing tool argument completes a bound pending slot:
  route agent_loop, target pending_clarification, not clarify. Clarify asks
  for information that is STILL missing; don't ask again after the user
  supplied the requested single value.
- When has_pending_action=false, a new, complete read-only tool request
  cannot be targeted at pending_clarification even if a saved plan exists.
- The word "task" is not evidence of a multi-step saved plan. A concrete
  one-off task created in a task manager must remain a tool action.
- Pending-action context and saved-plan context are separate: if a user gives
  a short missing parameter value while has_pending_action=true, it is a
  pending-clarification reply, not a request for more clarification.
- With has_pending_action=false, NEVER mark an independent tool request as
  pending_clarification. Use tool_action and reference.kind=none.
- Creating ONE task via create_task is agent_loop with mutation_requested,
  NOT plan_create. plan_create applies ONLY to a durable multi-step
  executable PLAN explicitly requested for FUTURE work, not a normal write.
- "Use calculate tool" and "query MCP definition of RAG" are agent_loop,
  read_only and single_tool, not respond/clarify when parameters are present.
- When has_pending_action=true and the user replies only with a value
  answering the prior missing argument, choose agent_loop,
  route_target=pending_clarification and reference.kind=pending_slot.
  Pending field is unverified unless the Host supplies its exact binding.
- An independent calculator request while a plan exists is agent_loop,
  NOT respond and NOT plan_resume, especially with "do not execute plan".
- The lack of a saved plan does not change the semantic request to resume it;
  return plan_resume and let Host deny missing plan.
- An unknown tool explicitly requested is agent_loop as an intent proposal;
  only Host registry checks can reject the unsupported tool.
- A request to continue saved remaining plan is plan_resume, saved_plan,
  remaining_steps and read_only unless mutation of external state is asked.
- Negative constraints such as "don't execute" narrow action scope.
  A plan edit that says "don't execute" still requests a plan revision,
  which is a durable plan mutation but NOT tool execution.
Use EXACTLY one route with a combination allowed by the schema.
Do not output inconsistent route_target, scope, effect or clarification.
Reference fields: kind none requires null step_number and null pending_field.
Cancel pending argument is not saved_step; step_number must remain null.
One-off task-manager item creation is agent_loop/tool_action, not a durable
ordered multi-step plan. Host independently checks write authorization.
For plan_create, plan_step_evidence must contain TWO distinct exact spans
for separately requested ordered steps; never invent missing plan steps.
Negative constraints are operation-scoped, not global write prohibitions.
Do not fabricate source spans or reference fields; use empty/default
values if original text does not prove the field."""


async def evaluate_shadow(provider: HTTPUnderstandingModel, model_name: str,
                          message: str, *, has_plan: bool,
                          pending_action: str | None, timeout_s: float = 12) -> dict:
    """Isolated evaluation: never invokes AgentLoop, tools, stores or permissions."""
    import asyncio
    began = time.perf_counter()
    stage = "provider"
    try:
        raw = await asyncio.wait_for(provider.complete(
            model=model_name, response_schema=ShadowRoute.model_json_schema(),
            messages=[{"role":"system","content":PROMPT},
                      {"role":"user","content":json.dumps({
                          "message":message,"has_saved_plan":has_plan,
                          "has_pending_action":pending_action is not None,
                          "pending_action_tool":pending_action
                      },ensure_ascii=False)}]), timeout=timeout_s)
        stage = "schema"
        result = ShadowRoute.model_validate_json(raw)
        stage = "source_provenance"
        verify_source_spans(result, message)
        stage = "host_preview"
        would_deny = hypothetical_admission(
            result, has_plan=has_plan, pending_action=pending_action)
        return {"state":"valid","route":result.primary_route,
                "route_target":result.route_target,
                "reference_kind":result.reference.kind,
                "scope":result.execution_scope,"effect":result.effect_intent,
                "ambiguity":result.ambiguity,"host_would_deny":would_deny,
                "host_admission_complete":False,
                "evidence_spans":len(result.source_evidence),
                "elapsed_ms":int((time.perf_counter()-began)*1000)}
    except ValidationError as exc:
        diagnostics = [{"path":".".join(str(i) for i in err["loc"])[:120],
                        "type":err["type"][:80],
                        "code": (str(err.get("ctx", {}).get("error", ""))[:80]
                                 if err["type"] == "value_error" else "")}
                       for err in exc.errors(include_url=False)[:8]]
        return {"state":"unavailable","error_class":"ValidationError",
                "validation_errors":diagnostics,
                "elapsed_ms":int((time.perf_counter()-began)*1000)}
    except ValueError as exc:
        # Fixed allowlist; never reflect raw exception or model/user content.
        safe = {"shadow_untrusted_source_quote",
                "shadow_untrusted_source_span",
                "shadow_source_offset_out_of_bounds",
                "shadow_source_offset_mismatch",
                "shadow_source_text_not_found",
                "shadow_mutation_conflicts_negative_constraint"}
        code = str(exc) if str(exc) in safe else "other_value_error"
        return {"state":"unavailable","error_class":"ValueError",
                "failure_stage":stage,"validation_code":code,
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
