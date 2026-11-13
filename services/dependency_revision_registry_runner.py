"""S25-07: real registry invocation for the bounded SECOND revision2 read step.

The Host/Claim/Fence and Dispatch were already committed by the caller. This
function commits an irrevocable 'invoking' marker BEFORE executing the trusted
local text_stats adapter. A crash after that marker creates UNKNOWN OUTCOME,
never a permission to repeat a tool call. It is intentionally internal-only:
no HTTP, model tool, generic retry, or externally attested exactly-once claim.
"""
from __future__ import annotations

import json

from sqlalchemy import update

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError
from agent.tool_registry import READ_REGISTRY
from services.dependency_fencing import DependencyFenceToken
from services.dependency_revision_second import _bound_step2
from services.evidence_store import bounded_projection


def _deny(code: str):
    raise DependencyPlanError(code)


def invoke_second_revised_registry_read(
    session_factory, *, settings: Settings, owner_id: str,
    conversation_id: str, turn_id: str, request_id: str,
    expected_raw: str, initial_turn_raw: str,
    fence: DependencyFenceToken, dispatch_id: str,
) -> dict:
    """Invoke one actual trusted text_stats adapter after durable intent.

    Only this runner owns the registry result. Failure becomes observed_failure.
    An unexpected crash leaves the committed 'invoking' marker intact and the
    original Claim/Fence active for explicit investigation. No second invoke.
    """
    if (type(settings) is not Settings
            or not settings.s25_07_revision_registry_invocation_enabled
            or type(dispatch_id) is not str or not dispatch_id):
        _deny("dependency_v2_registry_invocation_disabled")

    # Durable, committed point of no return. Distinguishes an explicitly
    # reserved intent (not yet called) from a potentially invoked read.
    with session_factory.begin() as db:
        _, _, _, args, entry = _bound_step2(
            db, settings=settings, owner_id=owner_id,
            conversation_id=conversation_id, turn_id=turn_id,
            request_id=request_id, expected_raw=expected_raw,
            initial_turn_raw=initial_turn_raw, fence=fence,
            dispatch_id=dispatch_id)
        if (entry.status != "dispatch_unknown"
                or entry.receipt_json is not None
                or READ_REGISTRY.version_for("text_stats") != "1.0.0"):
            _deny("dependency_v2_registry_invocation_replayed")
        # SELECT FOR UPDATE is ignored by SQLite. A durable conditional UPDATE
        # elects one invoker even when another worker read the same intent.
        elected = db.execute(update(type(entry)).where(
            type(entry).id == dispatch_id,
            type(entry).status == "dispatch_unknown",
            type(entry).receipt_json.is_(None),
            type(entry).worker_generation == fence.generation,
        ).values(status="invoking"))
        if elected.rowcount != 1:
            _deny("dependency_v2_invocation_race_lost")
        db.flush()

    # Actual registered adapter execution. No lock is held over tool code.
    # Its output remains untrusted until the bound args and receipt are
    # revalidated during the next DB transaction.
    result = READ_REGISTRY.invoke(
        "text_stats", args,
        granted_names=("text_stats",), exposed_names=("text_stats",),
        expected_version="1.0.0")
    if (type(result) is not dict or type(result.get("ok")) is not bool
            or result.get("tool_name") != "text_stats"
            or result.get("tool_version") != "1.0.0"):
        _deny("dependency_v2_registry_invalid_result")
    projection = bounded_projection("text_stats", result)
    if type(projection.get("ok")) is not bool:
        _deny("dependency_v2_registry_invalid_result")
    if projection["ok"]:
        if (type(projection.get("characters")) is not int
                or type(projection.get("words")) is not int
                or projection["characters"] != len(args["text"])
                or projection["words"] != len(args["text"].split())):
            _deny("dependency_v2_registry_invalid_result")
    with session_factory.begin() as db:
        _, _, _, renewed, entry = _bound_step2(
            db, settings=settings, owner_id=owner_id,
            conversation_id=conversation_id, turn_id=turn_id,
            request_id=request_id, expected_raw=expected_raw,
            initial_turn_raw=initial_turn_raw, fence=fence,
            dispatch_id=dispatch_id)
        if (entry.status != "invoking" or entry.receipt_json is not None
                or renewed != args):
            _deny("dependency_v2_registry_outcome_conflict")
        entry.receipt_json = json.dumps(projection, ensure_ascii=False)
        entry.status = "observed" if projection["ok"] else "observed_failure"
        db.flush()
    return projection
