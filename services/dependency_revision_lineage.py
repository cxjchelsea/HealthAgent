"""S25-07 Gate B3a: read-only, fail-closed physical revision-2 lineage proof.

This object is NOT a Host admission, Claim, Dispatch, retry, or execution
permit. B3b must recheck inside its own Plan -> Claim -> Dispatch lock order.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from agent.dependency_plan_schema import (
    DependencyPlanError, DependencyStep, PINNED_TOOL_VERSION,
    PLAN_SCHEMA_VERSION,
)
from agent.dependency_revision_candidate import REVISED_EXECUTOR_VERSION
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, PlanToolDispatch, PrimaryRouteEvidence, Turn,
)
from services.dependency_revision_fingerprint import revision_fingerprint
from services.evidence_store import arguments_digest, bounded_projection


@dataclass(frozen=True)
class VerifiedRevisionLineage:
    conversation_id: str
    plan_digest: str
    revision_turn_id: str
    preserved_step_ids: tuple[int, ...]
    next_step_id: int


def _deny():
    raise DependencyPlanError("dependency_revision_lineage_untrusted")


def _dict(value):
    if type(value) is not dict:
        _deny()
    return value


def _decode(value):
    if type(value) is not str:
        _deny()
    try:
        return _dict(json.loads(value))
    except (TypeError, ValueError):
        _deny()


def verify_revised_lineage(session_factory, *, owner_id: str,
                           conversation_id: str,
                           expected_raw: str) -> VerifiedRevisionLineage:
    """Prove persisted revision provenance, with no side effects.

    No original revision-1 raw plan is reconstructed from a SHA digest:
    only the signed-off B2 Host evidence and matching physical history are
    considered. This proof is per-snapshot and MUST NOT be cached as authority.
    """
    if not all(type(v) is str and v for v in (
            owner_id, conversation_id, expected_raw)):
        _deny()
    plan = _decode(expected_raw)
    if (set(plan) != {"plan_schema_version", "plan_executor_version",
                      "revision", "next_step_id", "steps", "executions",
                      "lineage"}
            or plan["plan_schema_version"] != PLAN_SCHEMA_VERSION
            or plan["plan_executor_version"] != REVISED_EXECUTOR_VERSION
            or type(plan["revision"]) is not int or plan["revision"] != 2):
        _deny()
    steps, executions, lineage = (
        plan.get("steps"), plan.get("executions"), plan.get("lineage"))
    lineage = _dict(lineage)
    if (set(lineage) != {"parent_snapshot_sha256", "parent_revision",
                         "preserved_step_ids", "origin_refs"}
            or type(lineage["parent_revision"]) is not int
            or lineage["parent_revision"] != 1
            or type(lineage["parent_snapshot_sha256"]) is not str
            or len(lineage["parent_snapshot_sha256"]) != 64
            or any(c not in "0123456789abcdef"
                   for c in lineage["parent_snapshot_sha256"])
            or type(steps) is not list or not 2 <= len(steps) <= 8
            or type(executions) is not dict
            or type(plan["next_step_id"]) is not int
            or type(lineage["preserved_step_ids"]) is not list
            or type(lineage["origin_refs"]) is not list):
        _deny()
    pending_seen = False
    completed = []
    parsed = {}
    for i, step in enumerate(steps):
        if type(step) is not dict or step.get("status") not in {"pending", "completed"}:
            _deny()
        try:
            parsed_step = DependencyStep.model_validate({
                **step, "status": "pending"})
        except (TypeError, ValueError):
            _deny()
        if (parsed_step.model_dump() != {**step, "status": "pending"}
                or parsed_step.id in parsed
                or (i and parsed_step.id <= steps[i-1]["id"])
                or parsed_step.tool_version != PINNED_TOOL_VERSION
                or len(parsed_step.depends_on) != len(set(parsed_step.depends_on))
                or any(dep not in parsed for dep in parsed_step.depends_on)):
            _deny()
        parsed[parsed_step.id] = parsed_step
        if step["status"] == "completed":
            if pending_seen:
                _deny()
            completed.append(step)
        else:
            pending_seen = True
        if parsed_step.expected_tool == "mcp_reference_lookup":
            b = parsed_step.argument_bindings
            if (parsed_step.depends_on or set(b) != {"term"}
                    or b["term"].kind != "literal"):
                _deny()
        elif parsed_step.expected_tool == "text_stats":
            b = parsed_step.argument_bindings
            if (set(b) != {"text"} or b["text"].kind != "observation_ref"
                    or b["text"].source_step_id not in parsed_step.depends_on
                    or b["text"].source_step_id not in parsed
                    or parsed[b["text"].source_step_id].expected_tool
                        != "mcp_reference_lookup"
                    or b["text"].field != "definition"):
                _deny()
        else:
            _deny()
    completed_ids = [x["id"] for x in completed]
    if (not pending_seen
            or plan["next_step_id"] != steps[-1]["id"] + 1
            or set(executions) != {str(i) for i in completed_ids}
            or lineage["preserved_step_ids"] != completed_ids
            or len(lineage["origin_refs"]) != len(completed_ids)):
        _deny()
    digest = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
    with session_factory() as db:
        conversation = db.get(Conversation, conversation_id)
        saved = db.get(ConversationPlan, conversation_id)
        if (conversation is None or conversation.owner_id != owner_id
                or saved is None or saved.snapshot != expected_raw):
            _deny()
        approval = db.scalars(select(PrimaryRouteEvidence).where(
            PrimaryRouteEvidence.conversation_id == conversation_id,
            PrimaryRouteEvidence.approved_revision_digest
                == revision_fingerprint(plan),
            PrimaryRouteEvidence.route_kind == "plan_revise",
            PrimaryRouteEvidence.disposition == "allow",
            PrimaryRouteEvidence.admission_reason == "dependency_revision_candidate",
        )).all()
        if len(approval) != 1:
            _deny()
        approval = approval[0]
        revision_turn = db.get(Turn, approval.turn_id)
        if (revision_turn is None
                or revision_turn.conversation_id != conversation_id
                or revision_turn.state != "partial"
                or revision_turn.failure_code is not None):
            _deny()
        task = db.get(AgentTaskState, conversation_id)
        projection = [
            {"id": step["id"], "status": step["status"],
             "evidence_tool": (step["expected_tool"]
                               if step["status"] == "completed" else None)}
            for step in steps
        ]
        try:
            actual_projection = json.loads(task.step_states) if task else None
        except (TypeError, ValueError):
            _deny()
        if (task is None or task.plan_revision != 2
                or task.state != ("partial" if completed else "planned")
                or actual_projection != projection):
            _deny()
        revisions = db.scalars(select(AgentStateTransition).where(
            AgentStateTransition.conversation_id == conversation_id,
            AgentStateTransition.reason_code == "dependency_plan_revision_saved"
        )).all()
        if (len(revisions) != 1 or revisions[0].task_id != task.task_id
                or revisions[0].revision != task.revision):
            _deny()
        dispatches = db.scalars(select(PlanToolDispatch).where(
            PlanToolDispatch.conversation_id == conversation_id)).all()
        if (len(dispatches) != len(completed)
                or {d.step_id for d in dispatches} != set(completed_ids)):
            _deny()
        dispatch_by_id = {d.id: d for d in dispatches}
        for step, origin in zip(completed, lineage["origin_refs"]):
            origin = _dict(origin)
            rec = _dict(executions[str(step["id"])])
            if (set(origin) != {"step_id", "dispatch_id", "plan_revision",
                               "tool_name", "tool_version", "arguments_digest"}
                    or set(rec) != {"step_id", "plan_revision", "dispatch_id",
                                   "tool_name", "tool_version",
                                   "resolved_arguments_digest",
                                   "source_evidence_refs"}
                    or rec["step_id"] != step["id"]
                    or rec["plan_revision"] != 1
                    or origin != {
                        "step_id": step["id"], "dispatch_id": rec["dispatch_id"],
                        "plan_revision": 1, "tool_name": rec["tool_name"],
                        "tool_version": rec["tool_version"],
                        "arguments_digest": rec["resolved_arguments_digest"]}
                    or rec["tool_name"] != step["expected_tool"]
                    or rec["tool_version"] != step["tool_version"]):
                _deny()
            d = dispatch_by_id.get(rec["dispatch_id"])
            a = db.get(AgentAttempt, rec["dispatch_id"])
            source_turn = db.get(Turn, d.turn_id) if d else None
            source_admission = db.get(PrimaryRouteEvidence, d.turn_id) if d else None
            if (d is None or d.step_id != step["id"]
                    or d.status != "checkpointed"
                    or d.tool_name != step["expected_tool"]
                    or d.tool_version != step["tool_version"]
                    or d.turn_id == revision_turn.id
                    or source_turn is None
                    or source_turn.conversation_id != conversation_id
                    or source_turn.state not in {"partial", "completed"}
                    or source_admission is None
                    or source_admission.conversation_id != conversation_id
                    or source_admission.disposition != "allow"
                    or source_admission.route_kind
                        not in {"plan_execute_first", "plan_resume"}
                    or source_admission.admission_reason
                        != "primary_v1_plan_execution_candidate"
                    or a is None or a.conversation_id != conversation_id
                    or a.turn_id != d.turn_id or a.step_id != step["id"]
                    or a.status != "succeeded"
                    or a.tool_name != d.tool_name
                    or a.tool_version != d.tool_version):
                _deny()
            args = _decode(d.arguments_json)
            receipt = _decode(d.receipt_json)
            projection_receipt = _decode(a.result_projection)
            if (receipt.get("ok") is not True
                    or projection_receipt != bounded_projection(
                        d.tool_name, receipt)
                    or rec["resolved_arguments_digest"] != arguments_digest(args)
                    or a.arguments_digest != arguments_digest(args)):
                _deny()
            if step["expected_tool"] == "mcp_reference_lookup":
                binding = step["argument_bindings"]["term"]
                if (args != {"term": binding["value"]}
                        or rec["source_evidence_refs"] != []
                        or type(receipt.get("definition")) is not str
                        or not receipt["definition"]
                        or receipt.get("definition_availability") == "truncated"
                        or bounded_projection(d.tool_name, receipt).get(
                            "definition") != receipt["definition"]):
                    _deny()
            else:
                binding = step["argument_bindings"]["text"]
                predecessor = _dict(executions.get(str(binding["source_step_id"])))
                source = dispatch_by_id.get(predecessor.get("dispatch_id"))
                if (source is None
                        or rec["source_evidence_refs"]
                            != [{"source_dispatch_id": source.id}]):
                    _deny()
                source_receipt = _decode(source.receipt_json)
                definition = source_receipt.get("definition")
                if (type(definition) is not str or not definition
                        or args != {"text": definition}
                        or type(receipt.get("characters")) is not int
                        or receipt["characters"] != len(definition)
                        or type(receipt.get("words")) is not int
                        or receipt["words"] != len(definition.split())):
                    _deny()
    return VerifiedRevisionLineage(
        conversation_id=conversation_id, plan_digest=digest,
        revision_turn_id=revision_turn.id,
        preserved_step_ids=tuple(completed_ids),
        next_step_id=next(x["id"] for x in steps
                         if x["status"] == "pending"))
