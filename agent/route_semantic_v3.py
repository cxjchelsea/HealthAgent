"""S25-02 M2 semantic-v3 shadow: one LLM interpretation, deterministic compilation.

Isolated diagnostic entry. No tools, checkpoint writes, side effects, or M3 cutover.
"""
import asyncio
import json
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.route_shadow import ShadowRoute, EvidenceSpan, verify_source_spans
from agent.route_shadow_host import ShadowHostFacts, preview_admission


class SemanticIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_type: Literal["none", "task", "plan", "plan_step", "pending_slot", "tool"]
    operation: Literal["answer", "clarify", "create", "revise", "execute_first",
                       "resume", "cancel", "invoke"]
    effect_intent: Literal["none", "read_only", "mutation_requested"]
    reference_text: str | None = Field(default=None, max_length=200)
    step_number: int | None = Field(default=None, ge=1, le=8)
    plan_steps: list[str] = Field(default_factory=list, max_length=8)
    source_quote: str | None = Field(default=None, max_length=400)
    clarification_question: str | None = Field(default=None, max_length=300)


SEMANTIC_PROMPT = """Interpret the user's current request, not tool permissions.
Return only the SemanticIntent JSON schema. Distinguish a single task record
from a persistent ordered plan by the requested object and lifecycle, not words.
A task creation is object_type task, operation create. A persistent ordered
execution plan is object_type plan, operation create, with distinct verbatim
step quotations in plan_steps. Never fabricate steps. To change a saved
step return plan_step/revise with a quoted reference_text and optional
step_number only when explicit. An answer to a pending argument is
pending_slot/invoke; effect is the original tool's external effect,
not whether the reply itself writes anything. For unclear targets or
arguments use clarify and provide a question. Independent tools are
tool/invoke. A request to resume a missing plan remains plan/resume.
For text references use exact quotations from the current request.
Do not invent authorization, checkpoints, arguments or source coordinates."""


def _unique_span(message: str, quote: str) -> EvidenceSpan:
    start = message.find(quote)
    if not quote or start < 0:
        raise ValueError("unverified_quote")
    if message.find(quote, start + 1) >= 0:
        raise ValueError("ambiguous_quote")
    return EvidenceSpan(start=start, end=start + len(quote), exact_text=quote)


def compile_semantic(intent: SemanticIntent, message: str,
                     facts: ShadowHostFacts) -> ShadowRoute:
    """Compile only proven intent/reference pairs. Never repair mistaken intent."""
    obj, op = intent.object_type, intent.operation
    routes = {
        ("none", "answer"): ("respond", "none", "none"),
        ("none", "clarify"): ("clarify", "none", "none"),
        ("task", "create"): ("agent_loop", "tool_action", "single_tool"),
        ("tool", "invoke"): ("agent_loop", "tool_action", "single_tool"),
        ("pending_slot", "invoke"): ("agent_loop", "pending_clarification", "single_tool"),
        ("plan", "create"): ("plan_create", "none", "none"),
        ("plan", "execute_first"): ("plan_execute_first", "saved_plan", "first_step"),
        ("plan", "resume"): ("plan_resume", "saved_plan", "remaining_steps"),
        ("plan_step", "revise"): ("plan_revise", "saved_plan", "none"),
        ("pending_slot", "cancel"): ("cancel", "pending_clarification", "none"),
        ("plan", "cancel"): ("cancel", "saved_plan", "none"),
    }
    if (obj, op) not in routes:
        raise ValueError("unsupported_semantic_pair")
    route, target, scope = routes[obj, op]
    ref = {"kind": "none"}
    if obj == "pending_slot" and op == "invoke":
        if not (facts.pending_action and facts.pending_field
                and facts.pending_original_request_bound):
            raise ValueError("pending_origin_unverified")
        ref = {"kind": "pending_slot", "pending_field": facts.pending_field}
    if obj == "plan_step" and op == "revise":
        if not (facts.has_plan and facts.revision_valid and intent.step_number
                and intent.step_number <= len(facts.plan_states)):
            raise ValueError("plan_step_binding_unverified")
        if not intent.reference_text:
            raise ValueError("plan_step_source_unverified")
        _unique_span(message, intent.reference_text)
        ref = {"kind": "saved_step", "step_number": intent.step_number}
    if route == "plan_create":
        if len(intent.plan_steps) < 2:
            raise ValueError("plan_steps_unverified")
    step_spans = [_unique_span(message, x) for x in intent.plan_steps] if route == "plan_create" else []
    ordered = sorted(step_spans, key=lambda x: x.start)
    if any(a.end > z.start for a, z in zip(ordered, ordered[1:])):
        raise ValueError("plan_step_overlap")
    source = [_unique_span(message, intent.source_quote)] if intent.source_quote else []
    if route == "plan_create" and not source:
        # Distinct, verified plan steps already ground the plan intent.
        # An extra model quote is optional, not a separate source of authority.
        source = [EvidenceSpan(start=ordered[0].start, end=ordered[-1].end,
                               exact_text=message[ordered[0].start:ordered[-1].end])]
    expected_effects = {
        ("task", "create"): "mutation_requested",
        ("plan", "create"): "mutation_requested",
        ("plan_step", "revise"): "mutation_requested",
        ("none", "answer"): "none",
        ("none", "clarify"): "none",
        ("pending_slot", "cancel"): "none",
        ("plan", "cancel"): "none",
    }
    expected_effect = expected_effects.get((obj, op))
    if expected_effect is not None and intent.effect_intent != expected_effect:
        raise ValueError("semantic_effect_conflict")
    if obj == "pending_slot" and op == "invoke" and intent.effect_intent == "none":
        raise ValueError("pending_effect_unverified")
    if route in {"respond", "clarify", "cancel"} and intent.effect_intent != "none":
        raise ValueError("non_effect_route_conflict")
    clarification = (
        {"required": True, "reason": "uncertain_intent",
         "question": intent.clarification_question}
        if route == "clarify" else None)
    if route == "clarify" and not intent.clarification_question:
        raise ValueError("missing_clarification_question")
    values = {
        "schema_version": "s25-route-v2", "primary_route": route,
        "route_target": target, "execution_scope": scope,
        "effect_intent": intent.effect_intent, "ambiguity": "clear",
        "reference": ref, "source_evidence": source,
        "plan_step_evidence": step_spans,
    }
    if clarification:
        values["clarification"] = clarification
    result = ShadowRoute.model_validate(values)
    verify_source_spans(result, message)
    return result


async def evaluate_semantic_v3(provider, model_name: str, message: str,
                               facts: ShadowHostFacts, timeout_s: float = 45) -> dict:
    """Return bounded diagnostics, never executable approval."""
    stage = "model"
    semantic = None
    try:
        raw = await asyncio.wait_for(provider.complete(
            model=model_name, response_schema=SemanticIntent.model_json_schema(),
            messages=[{"role": "system", "content": SEMANTIC_PROMPT},
                      {"role": "user", "content": json.dumps({
                          "message": message, "has_saved_plan": facts.has_plan,
                          "has_pending_action": bool(facts.pending_action),
                          "pending_field": facts.pending_field,
                          "known_step_count": len(facts.plan_states)}, ensure_ascii=False)}]),
            timeout=timeout_s)
        stage = "semantic_schema"
        semantic = SemanticIntent.model_validate_json(raw)
        stage = "reference_compilation"
        compiled = compile_semantic(semantic, message, facts)
        stage = "host_preview"
        preview = preview_admission(compiled, facts)
        return {"state": "valid", "semantic_object": semantic.object_type,
                "semantic_operation": semantic.operation, "route": compiled.primary_route,
                "effect": compiled.effect_intent, "route_target": compiled.route_target,
                "host_preview": asdict(preview), "host_admission_complete": False}
    except (ValidationError, ValueError) as exc:
        code = str(exc) if str(exc) in {
            "unsupported_semantic_pair", "unverified_quote", "ambiguous_quote",
            "pending_origin_unverified", "plan_step_binding_unverified",
            "plan_step_source_unverified", "plan_steps_unverified",
            "plan_step_overlap", "plan_intent_source_unverified",
            "task_effect_conflict", "semantic_effect_conflict", "pending_effect_unverified",
            "non_effect_route_conflict", "missing_clarification_question"
        } else "schema_or_contract_invalid"
        return {"state": "unavailable", "stage": stage, "reason": code,
                **({"semantic_object": semantic.object_type,
                    "semantic_operation": semantic.operation,
                    "semantic_effect": semantic.effect_intent}
                   if semantic is not None else {}),
                "host_admission_complete": False}
    except Exception as exc:
        return {"state": "unavailable", "stage": stage,
                "error_class": type(exc).__name__, "host_admission_complete": False}
