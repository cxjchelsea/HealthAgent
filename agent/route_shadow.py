"""S25-02 M2: side-effect-free V2 semantic shadow, never Host execution authority."""
import json
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
        return self


PROMPT = """Return exactly JSON matching the provided schema. Identify ONE current-turn
semantic route proposal, not authorization. Existing saved plans and pending
clarifications are context only. An independent tool request must not resume a
saved plan. A written draft is NOT a persistent plan. Do not infer permission
to write from history. If ambiguous select clarify. Do not invent plan/step
authority, tool availability, completed steps or observations. source_quote
must be exact text from CURRENT user message (or empty if unavailable).
Never execute tools. This is a diagnostic shadow-only classification."""


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
        if result.source_quote and result.source_quote not in message:
            raise ValueError("shadow_untrusted_source_quote")
        # This is NOT an admission. The bound Host state supplies conservative
        # hypothetical rejection reasons for comparison, never execution.
        would_deny = None
        if result.primary_route in {"plan_execute_first","plan_resume","plan_revise"} and not has_plan:
            would_deny = "missing_saved_plan"
        elif result.primary_route == "plan_create" and has_plan:
            would_deny = "existing_saved_plan"
        elif result.route_target == "pending_clarification" and pending_action is None:
            would_deny = "missing_pending_action"
        return {"state":"valid","route":result.primary_route,
                "scope":result.execution_scope,"effect":result.effect_intent,
                "ambiguity":result.ambiguity,"host_would_deny":would_deny,
                "elapsed_ms":int((time.perf_counter()-began)*1000)}
    except Exception as exc:
        return {"state":"unavailable","error_class":type(exc).__name__,
                "elapsed_ms":int((time.perf_counter()-began)*1000)}
