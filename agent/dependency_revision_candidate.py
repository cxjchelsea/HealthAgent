"""S25-07 Gate B1: pure, non-authorizing revision candidate compiler.

A model may propose pending-step edits, but can NEVER supply the completed
prefix, historical execution records, provenance metadata, or version number.
This module neither validates DB evidence nor persists or executes a revision.
The output uses a distinct, deliberately non-executable runtime version until
Gate B2/B3 Host admission and cross-revision execution are implemented.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from agent.dependency_plan_host import _step_for_read
from agent.dependency_plan_schema import (
    DependencyPlanError, DependencyStep, LiteralBinding, ObservationBinding,
    PINNED_TOOL_VERSION, PLAN_SCHEMA_VERSION,
)

REVISED_EXECUTOR_VERSION = "dependency-plan-read-replan-v1"
_ORIGINAL_EXECUTOR_VERSION = "dependency-plan-read-v1"
_TOOLS = frozenset({"mcp_reference_lookup", "text_stats"})


@dataclass(frozen=True)
class RevisionCandidate:
    """Not a Host permit and not a durable, verified lineage statement."""
    expected_raw_sha256: str
    parent_revision: int
    candidate: dict


def _deny(code: str) -> None:
    raise DependencyPlanError(code)


def _validate_binding(step: DependencyStep, predecessors: dict[int, DependencyStep],
                      *, source_message: str | None = None) -> None:
    if step.expected_tool not in _TOOLS or step.tool_version != PINNED_TOOL_VERSION:
        _deny("dependency_tool_version_mismatch")
    param = "term" if step.expected_tool == "mcp_reference_lookup" else "text"
    if set(step.argument_bindings) != {param}:
        _deny("dependency_invalid_arguments")
    binding = step.argument_bindings[param]
    if isinstance(binding, LiteralBinding):
        maximum = 80 if param == "term" else 4000
        if (len(binding.value) > maximum or not binding.source_quote.strip()
                or binding.value.casefold() not in binding.source_quote.casefold()):
            _deny("dependency_source_unbound")
        if (source_message is not None
                and binding.source_quote.casefold() not in source_message.casefold()):
            _deny("dependency_source_unbound")
        if step.expected_tool == "mcp_reference_lookup" and step.depends_on:
            _deny("dependency_invalid_schema")
    elif isinstance(binding, ObservationBinding):
        predecessor = predecessors.get(binding.source_step_id)
        if (step.expected_tool != "text_stats"
                or binding.source_step_id not in step.depends_on
                or predecessor is None
                or predecessor.expected_tool != "mcp_reference_lookup"
                or predecessor.tool_version != PINNED_TOOL_VERSION
                or binding.field != "definition"):
            _deny("dependency_source_not_dependency")
    else:
        _deny("dependency_invalid_arguments")


def compile_revision_candidate(
    *, expected_raw: str, replacement_pending: list[dict],
    current_user_message: str,
) -> RevisionCandidate:
    """Build revision 2 from *exact* S25-06 revision-1 parent, without DB writes.

    In Slice B1, step IDs/order/dependencies must remain intact; only pending
    step goals/tool bindings may change. No partially dispatched pending step
    is authorized for substitution by this pure compiler; B2 must inspect the
    durable Dispatch journal under locks before accepting any candidate.

    New literal values must be grounded verbatim in the current user message.
    This is intentionally conservative and does not treat external tool
    observations as instructions or as user consent.
    """
    if (type(expected_raw) is not str or not expected_raw
            or type(current_user_message) is not str
            or not current_user_message.strip()
            or type(replacement_pending) is not list):
        _deny("dependency_revision_invalid_input")
    try:
        parent = json.loads(expected_raw)
    except (ValueError, TypeError):
        _deny("dependency_invalid_schema")
    if (type(parent) is not dict
            or parent.get("plan_schema_version") != PLAN_SCHEMA_VERSION
            or parent.get("plan_executor_version") != _ORIGINAL_EXECUTOR_VERSION
            or parent.get("revision") != 1):
        _deny("dependency_revision_unsupported_parent")
    steps = parent.get("steps")
    if type(steps) is not list:
        _deny("dependency_invalid_schema")
    pending_ids = [s.get("id") for s in steps
                   if type(s) is dict and s.get("status") == "pending"]
    if not pending_ids or len(pending_ids) != len(replacement_pending):
        _deny("dependency_revision_suffix_mismatch")
    # Existing S25-06 runtime validator is retained untouched. A revision
    # must start from a fully valid existing checkpoint, not a model copy.
    _step_for_read(parent, pending_ids[0])
    if any(type(x) is not dict for x in replacement_pending):
        _deny("dependency_revision_suffix_mismatch")
    old_pending = [s for s in steps if s["status"] == "pending"]
    if [s.get("id") for s in replacement_pending] != pending_ids:
        _deny("dependency_revision_suffix_mismatch")
    if any(s.get("status") != "pending" for s in replacement_pending):
        _deny("dependency_revision_suffix_mismatch")

    # Pydantic's strict creation step schema still applies to *all* edits.
    replacements = []
    for proposed in replacement_pending:
        try:
            replacements.append(DependencyStep.model_validate(proposed))
        except (ValidationError, TypeError, ValueError):
            _deny("dependency_invalid_schema")
    normalized = copy.deepcopy(steps)
    start = len(steps) - len(pending_ids)
    if start < 0 or steps[start:] != old_pending:
        _deny("dependency_invalid_schema")
    changed = False
    for offset, proposed in enumerate(replacements):
        old = old_pending[offset]
        if (proposed.id != old["id"]
                or proposed.depends_on != old["depends_on"]
                or proposed.status != "pending"):
            _deny("dependency_revision_suffix_mismatch")
        proposed_dict = proposed.model_dump()
        if proposed_dict != old:
            changed = True
            normalized[start + offset] = proposed_dict
    if not changed:
        _deny("dependency_revision_no_change")

    predecessor_by_id: dict[int, DependencyStep] = {}
    for i, raw in enumerate(normalized):
        try:
            parsed = DependencyStep.model_validate({
                **raw, "status": "pending",
            })
        except (ValidationError, TypeError, ValueError):
            _deny("dependency_invalid_schema")
        if any(x not in predecessor_by_id for x in parsed.depends_on):
            _deny("dependency_order_invalid")
        # Only a new or changed literal binding needs a fresh user quote.
        # Renaming a pending goal does not invalidate an unchanged, previously
        # grounded argument from the saved plan.
        prior_bindings = steps[i]["argument_bindings"]
        param = "term" if parsed.expected_tool == "mcp_reference_lookup" else "text"
        new_binding = raw["argument_bindings"][param]
        binding_changed = (i >= start and
                           (raw["expected_tool"] != steps[i]["expected_tool"]
                            or new_binding != prior_bindings.get(param)))
        _validate_binding(
            parsed, predecessor_by_id,
            source_message=current_user_message if binding_changed else None)
        predecessor_by_id[parsed.id] = parsed

    # These refs are copied verbatim; this is only a tentative candidate.
    # The B2 transaction must re-fetch and verify each Receipt/Attempt under
    # Plan -> Claim -> Dispatch locks before committing a new snapshot.
    preserved = tuple(step["id"] for step in steps[:start])
    origin_refs = [
        {
            "step_id": sid,
            "dispatch_id": parent["executions"][str(sid)]["dispatch_id"],
            "plan_revision": parent["executions"][str(sid)]["plan_revision"],
            "tool_name": parent["executions"][str(sid)]["tool_name"],
            "tool_version": parent["executions"][str(sid)]["tool_version"],
            "arguments_digest": parent["executions"][str(sid)]["resolved_arguments_digest"],
        }
        for sid in preserved
    ]
    digest = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
    result = {
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "plan_executor_version": REVISED_EXECUTOR_VERSION,
        "revision": 2,
        "next_step_id": parent["next_step_id"],
        "steps": normalized,
        "executions": copy.deepcopy(parent["executions"]),
        "lineage": {
            "parent_snapshot_sha256": digest,
            "parent_revision": 1,
            "preserved_step_ids": list(preserved),
            "origin_refs": origin_refs,
        },
    }
    return RevisionCandidate(expected_raw_sha256=digest, parent_revision=1,
                             candidate=result)
