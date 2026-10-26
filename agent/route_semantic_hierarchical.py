"""Isolated hierarchical object then operation classification for S25-02.
No executable authority or route activation.
"""
import asyncio
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict
from agent.route_semantic_ab import CoreIntent

class ObjectChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_type: Literal["none", "task", "plan", "plan_step", "pending_slot", "tool"]

class OperationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["answer", "clarify", "create", "revise",
                       "execute_first", "resume", "cancel", "invoke"]

OBJECT_PROMPT = """Choose the requested object independently from the operation.
none: conversational answer, clarification or text-only unsaved draft.
task: persisted task-manager item, NOT a generic instruction.
tool: calculator, retrieval, shell or another requested tool operation.
plan: persistent ordered plan as a whole; plan_step: one saved step.
pending_slot: an answer or cancellation tied to an existing Host pending tool argument.
Do not mistake saved plan context for an instruction to modify that plan.
This is an interpretation only, never authorization."""

OP_PROMPT = """Given a classified object, choose exactly one requested operation.
none: answer or clarify. task: create. tool: invoke.
plan: create a new saved plan, execute_first on existing plan, resume
existing plan, or cancel. plan_step: revise an existing step.
pending_slot: invoke the paused tool with a supplied argument, or cancel it.
When required parameters are missing, use clarify. First-step execution
is not resuming all remaining steps. Do not infer permissions."""

async def classify_hierarchical(provider, model, message, facts, timeout=60):
    context={"message":message,"has_saved_plan":facts.has_plan,
             "has_pending_action":bool(facts.pending_action)}
    raw=await asyncio.wait_for(provider.complete(
        model=model,response_schema=ObjectChoice.model_json_schema(),
        messages=[{"role":"system","content":OBJECT_PROMPT},
                  {"role":"user","content":json.dumps(context,ensure_ascii=False)}]),
        timeout=timeout)
    obj=ObjectChoice.model_validate_json(raw)
    raw_op=await asyncio.wait_for(provider.complete(
        model=model,response_schema=OperationChoice.model_json_schema(),
        messages=[{"role":"system","content":OP_PROMPT},
                  {"role":"user","content":json.dumps({
                      **context,"selected_object":obj.object_type},
                      ensure_ascii=False)}]),timeout=timeout)
    op=OperationChoice.model_validate_json(raw_op)
    return CoreIntent(object_type=obj.object_type,operation=op.operation)
