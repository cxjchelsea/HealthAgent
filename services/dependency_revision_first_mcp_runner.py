"""Internal-only real trusted MCP invocation for the FIRST revision2 read.

A committed, Host-authorized first v2 Dispatch is mandatory. A write-once
'invoking' marker precedes the checked-in local MCP subprocess invocation.
Unknown outcomes do not grant replay. The original strict v2 checkpoint
writer is retained unchanged; this runner only owns the one-time Receipt.
No HTTP or model-controlled MCP command/registry discovery is supported.
"""
from __future__ import annotations

import json

from sqlalchemy import update

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError, PINNED_TOOL_VERSION
from agent.dependency_revision_host import PreparedRevisedRead
from agent.mcp_stdio_client import StdioMCPClient
from agent.tool_registry import READ_REGISTRY, ReadToolRuntime
from services.dependency_fencing import DependencyFenceToken
from services.dependency_revision_result import _bound_rows
from services.evidence_store import bounded_projection


def _deny(code: str):
    raise DependencyPlanError(code)


def invoke_first_revised_mcp_reference(
    session_factory, *, settings: Settings, binding: PreparedRevisedRead,
    expected_raw: str, initial_turn_raw: str, fence: DependencyFenceToken,
    dispatch_id: str, mcp_client: StdioMCPClient,
) -> dict:
    """Call trusted MCP once after committed revision2 first-step Dispatch.

    StdioMCPClient.call_trusted_reference() verifies a fixed local server
    identity, exact single-tool catalog, protocol version and response frames.
    Neither the model nor remote discovery can supply a command or grant.
    """
    if (type(settings) is not Settings
            or not settings.s25_07_revision_registry_invocation_enabled
            or type(binding) is not PreparedRevisedRead
            or binding.tool_name != "mcp_reference_lookup"
            or binding.tool_version != PINNED_TOOL_VERSION
            or binding.inherited_source_dispatch_id is not None
            or type(mcp_client) is not StdioMCPClient
            or READ_REGISTRY.version_for("mcp_reference_lookup")
                != PINNED_TOOL_VERSION
            or type(dispatch_id) is not str or not dispatch_id):
        _deny("dependency_v2_mcp_invocation_not_authorized")
    with session_factory.begin() as db:
        _, entry, _, step = _bound_rows(
            db, settings=settings, binding=binding,
            expected_raw=expected_raw, initial_turn_raw=initial_turn_raw,
            fence=fence, dispatch_id=dispatch_id)
        if (entry.status != "dispatch_unknown"
                or entry.receipt_json is not None
                or step.get("expected_tool") != "mcp_reference_lookup"
                or step.get("depends_on") != []
                or binding.arguments != json.loads(entry.arguments_json)):
            _deny("dependency_v2_mcp_invocation_replayed")
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

    # Do not hold Plan/Claim/Fence locks while a trusted subprocess is running.
    # Any crash after 'invoking' leaves unknown outcome, not a retry permit.
    result = READ_REGISTRY.invoke(
        "mcp_reference_lookup", binding.arguments,
        granted_names=("mcp_reference_lookup",),
        exposed_names=("mcp_reference_lookup",),
        expected_version=PINNED_TOOL_VERSION,
        runtime=ReadToolRuntime(mcp_client=mcp_client),
    )
    if (type(result) is not dict
            or type(result.get("ok")) is not bool
            or result.get("tool_name") != "mcp_reference_lookup"
            or result.get("tool_version") != PINNED_TOOL_VERSION):
        _deny("dependency_v2_mcp_invocation_invalid_result")
    projection = bounded_projection("mcp_reference_lookup", result)
    if type(projection.get("ok")) is not bool:
        _deny("dependency_v2_mcp_invocation_invalid_result")
    if projection["ok"]:
        definition = projection.get("definition")
        if (type(definition) is not str or not definition
                or projection.get("definition_availability") == "truncated"
                or result.get("definition") != definition):
            _deny("dependency_v2_mcp_invocation_unusable_success")
    with session_factory.begin() as db:
        _, entry, _, step = _bound_rows(
            db, settings=settings, binding=binding,
            expected_raw=expected_raw, initial_turn_raw=initial_turn_raw,
            fence=fence, dispatch_id=dispatch_id)
        if (entry.status != "invoking" or entry.receipt_json is not None
                or binding.arguments != json.loads(entry.arguments_json)):
            _deny("dependency_v2_mcp_invocation_outcome_conflict")
        entry.receipt_json = json.dumps(projection, ensure_ascii=False)
        entry.status = "observed" if projection["ok"] else "observed_failure"
        db.flush()
    return projection
