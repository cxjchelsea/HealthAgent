"""M5 typed primary semantic proposal. Proposals never authorize execution.

This module only validates model-proposed meaning; Host source binding and
final invoker authorization are separate responsibilities.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.route_shadow import EvidenceSpan, ShadowClarification, ShadowReference


class StrictProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedStep(StrictProposal):
    goal: str = Field(min_length=1, max_length=400)
    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class CreateOperation(StrictProposal):
    route: Literal["plan_create"]
    save_only: Literal[True]
    steps: list[ProposedStep] = Field(min_length=2, max_length=8)


class ReviseOperation(StrictProposal):
    route: Literal["plan_revise"]
    step_id: int = Field(ge=1, le=8)
    expected_old_arguments: dict
    proposed_arguments: dict
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class FirstOperation(StrictProposal):
    route: Literal["plan_execute_first"]
    scope: Literal["first_step"]
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class ResumeOperation(StrictProposal):
    route: Literal["plan_resume"]
    scope: Literal["remaining_steps", "all_steps"]
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class CancelPlanOperation(StrictProposal):
    route: Literal["plan_cancel"]
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class ToolCandidate(StrictProposal):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class AgentLoopOperation(StrictProposal):
    route: Literal["agent_loop"]
    candidate_tool: ToolCandidate | None = None


class CancelOperation(StrictProposal):
    route: Literal["cancel"]
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class RespondOperation(StrictProposal):
    route: Literal["respond"]


class ClarifyOperation(StrictProposal):
    route: Literal["clarify"]


OperationProposal = Annotated[
    Union[CreateOperation, ReviseOperation, FirstOperation, ResumeOperation,
          CancelPlanOperation, AgentLoopOperation, CancelOperation,
          RespondOperation, ClarifyOperation],
    Field(discriminator="route"),
]


_SCOPE = {
    "plan_create": {"none"},
    "plan_revise": {"none"},
    "plan_execute_first": {"first_step"},
    "plan_resume": {"remaining_steps", "all_steps"},
    "plan_cancel": {"none"},
    "agent_loop": {"none", "single_tool"},
    "cancel": {"none"},
    "respond": {"none"},
    "clarify": {"none"},
}
_EFFECT = {
    "plan_create": {"mutation_requested"},
    "plan_revise": {"mutation_requested"},
    "plan_execute_first": {"read_only", "mutation_requested"},
    "plan_resume": {"read_only", "mutation_requested"},
    "plan_cancel": {"mutation_requested"},
    "agent_loop": {"none", "read_only", "mutation_requested"},
    "cancel": {"none"},
    "respond": {"none"},
    "clarify": {"none"},
}


class PrimarySemanticProposal(StrictProposal):
    schema_version: Literal["s25-m5-primary-v1"]
    route: Literal["respond", "clarify", "agent_loop", "plan_create",
                   "plan_execute_first", "plan_resume", "plan_revise",
                   "plan_cancel", "cancel"]
    scope: Literal["none", "single_tool", "first_step",
                   "remaining_steps", "all_steps"]
    effect_intent: Literal["none", "read_only", "mutation_requested"]
    ambiguity: Literal["clear", "needs_disambiguation", "unreliable"]
    evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=12)
    reference: ShadowReference = Field(default_factory=ShadowReference)
    clarification: ShadowClarification = Field(default_factory=ShadowClarification)
    operation: OperationProposal

    @model_validator(mode="after")
    def consistent(self):
        if self.operation.route != self.route:
            raise ValueError("m5_operation_route_mismatch")
        if self.scope not in _SCOPE[self.route]:
            raise ValueError("m5_invalid_scope")
        if self.effect_intent not in _EFFECT[self.route]:
            raise ValueError("m5_invalid_effect")
        if self.ambiguity != "clear" and self.route not in {"clarify", "respond"}:
            raise ValueError("m5_ambiguous_effect_route")
        if self.route == "clarify" and not self.clarification.required:
            raise ValueError("m5_missing_clarification")
        if self.route != "clarify" and self.clarification.required:
            raise ValueError("m5_unexpected_clarification")
        if self.route == "agent_loop" and self.scope == "single_tool" and (
                self.operation.candidate_tool is None):
            raise ValueError("m5_missing_tool_candidate")
        if self.route == "agent_loop" and self.scope == "none" and (
                self.operation.candidate_tool is not None):
            raise ValueError("m5_unexpected_tool_candidate")
        if self.route == "plan_execute_first" and self.operation.scope != self.scope:
            raise ValueError("m5_operation_scope_mismatch")
        if self.route == "plan_resume" and self.operation.scope != self.scope:
            raise ValueError("m5_operation_scope_mismatch")
        return self
