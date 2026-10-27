"""Diagnostic-only two-stage semantic classifier; never grants Host authority."""
import asyncio
import json
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict
from agent.route_semantic_ab import CoreIntent

@dataclass(frozen=True)
class HierarchicalDiagnostic:
    """Retain stage-one evidence even if stage two fails."""
    object_type: str | None
    operation: str | None
    failure_stage: str | None = None
    error_class: str | None = None


class HierarchicalStageError(Exception):
    def __init__(self, stage: str, object_type: str | None, error_class: str):
        self.stage = stage
        self.object_type = object_type
        self.error_class = error_class
        super().__init__(f"{stage}: {error_class}")


class ObjectChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_type: Literal["none", "task", "plan", "plan_step", "pending_slot", "tool"]

class OperationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["answer", "clarify", "create", "revise",
                       "execute_first", "resume", "cancel", "invoke"]

OBJECT_PROMPT = """Identify the semantic TARGET of the CURRENT user request; do not choose an operation.
none: conversational answer, clarification or text-only unsaved draft.
task: persisted task-manager item, NOT a generic instruction.
tool: calculator, retrieval, shell or another requested tool operation.
plan: a persistent ordered plan as a whole. Requests to run its first step,
continue its remaining steps, create it or cancel it ALL target plan.
The ordinal "first" denotes an execution SCOPE, not a separate object.
plan_step: editing/replacing the CONTENT of a particular saved step;
not running a step, or continuing a plan.
pending_slot: a CURRENT reply supplying a requested missing tool argument,
or canceling the pending request. Use the trusted context only to understand
what is being replied to, not to assume every message concerns the pending
request. A new independent tool request still targets tool.
Do not mistake saved plan context for an instruction to modify that plan.
A request to resume a plan targets plan even if Host reports no saved plan.
A request for a tool targets tool even if Host reports no registered tool.
Neither object existence nor execution feasibility is a semantic category.
An unsaved textual draft is none; only explicit persistent task-management
requests target task.
This is an interpretation only, never authorization."""

OP_PROMPT = """For the selected semantic object, identify the CURRENT requested operation.
none: answer or clarify. task: create. tool: invoke.
plan: create a new persistent plan; execute_first starts the first
step (whether or not the plan actually exists); resume continues unfinished
steps after earlier steps; cancel stops a plan. plan_step: revise step content.
A request to run the first step is execute_first, not resume; a request to
continue the remainder is resume, not execute_first.
pending_slot: invoke the paused tool with a supplied argument, or cancel it.
A pending_slot reply filling the missing argument is invoke; a request
to abandon that pending interaction is cancel. An independent tool request
is invoke even when its parameters are unavailable or the tool is absent:
the Host, not the classifier, decides whether clarification is needed. First-step execution is not resuming remaining steps.
Classify the action the user REQUESTED, not the action currently allowed.
Do not infer permissions."""

def _context(message, facts):
    pending_bound = bool(
        facts.pending_action and facts.pending_field
        and facts.pending_original_request_bound
    )
    return {
        "message": message,
        "trusted_host_state": {
            "saved_plan_exists": bool(facts.has_plan),
            "saved_plan_step_count": len(facts.plan_states),
            "pending_tool_request_bound": pending_bound,
            "pending_missing_field": facts.pending_field if pending_bound else None,
            "pending_tool_action": facts.pending_action if pending_bound else None,
        },
        "context_rule": (
            "Host state is evidence for reference resolution, not a restriction "
            "on what semantic request the user can express. A pending request "
            "does not prevent an unrelated new request."
        ),
    }


async def classify_hierarchical_diagnostic(
    provider, model, message, facts, timeout=60
):
    """Both stages are model-driven; preserve partial stage evidence."""
    context = _context(message, facts)
    try:
        raw = await asyncio.wait_for(provider.complete(
            model=model, response_schema=ObjectChoice.model_json_schema(),
            messages=[{"role": "system", "content": OBJECT_PROMPT},
                      {"role": "user", "content": json.dumps(
                          context, ensure_ascii=False)}]), timeout=timeout)
        obj = ObjectChoice.model_validate_json(raw)
    except Exception as exc:
        return HierarchicalDiagnostic(None, None, "object", type(exc).__name__)
    try:
        raw_op = await asyncio.wait_for(provider.complete(
            model=model, response_schema=OperationChoice.model_json_schema(),
            messages=[{"role": "system", "content": OP_PROMPT},
                      {"role": "user", "content": json.dumps({
                          **context, "selected_object": obj.object_type},
                          ensure_ascii=False)}]), timeout=timeout)
        op = OperationChoice.model_validate_json(raw_op)
    except Exception as exc:
        return HierarchicalDiagnostic(
            obj.object_type, None, "operation", type(exc).__name__)
    return HierarchicalDiagnostic(obj.object_type, op.operation)


async def classify_hierarchical(provider, model, message, facts, timeout=60):
    """Legacy core-result adapter. Never returns a partial result as valid."""
    diagnostic = await classify_hierarchical_diagnostic(
        provider, model, message, facts, timeout)
    if diagnostic.failure_stage is not None:
        raise HierarchicalStageError(
            diagnostic.failure_stage, diagnostic.object_type,
            diagnostic.error_class or "UnknownError")
    assert diagnostic.object_type is not None
    assert diagnostic.operation is not None
    return CoreIntent(
        object_type=diagnostic.object_type,
        operation=diagnostic.operation)


# Both classification calls are in classify_hierarchical_diagnostic.
# The legacy adapter intentionally never changes the model's choices.

