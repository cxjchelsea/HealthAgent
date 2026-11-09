"""Saved-plan read execution boundary, separate from ordinary read grants.

M4 claim/owner/CAS are Host-owned. This Invoker never mints permission and
does not substitute ordinary Registry grants or upgrade legacy adapters.
It preserves the established M4 guard -> prepared dispatch -> tool -> receipt
ordering, with the exact legacy plan tool version contract.
"""
from dataclasses import dataclass
from typing import Callable, Any

from agent import plan_tool_versions
from agent.plan_intent import READ_TOOLS
from agent.tools import SCHEMAS, execute_tool


@dataclass(frozen=True)
class PlanReadRequest:
    step_id: int
    name: str
    arguments: dict
    tool_version: str | None


class PlanReadInvoker:
    def __init__(self, *, execute: Callable = execute_tool):
        self._execute = execute

    def invoke(self, request: PlanReadRequest, *, execution_guard: Callable | None,
               dispatch_journal: Any = None, clock=None, task_store=None,
               memory_store=None, mcp_client=None) -> dict:
        # This is defense in depth; Host admission and its durable execution
        # claim remain mandatory for HTTP saved-plan execution.
        if not isinstance(request, PlanReadRequest):
            return {"ok": False, "error": "invalid_plan_invocation"}
        if (type(request.step_id) is not int or request.step_id < 1
                or request.name not in READ_TOOLS or request.name not in SCHEMAS
                or not isinstance(request.arguments, dict)):
            return {"ok": False, "error": "invalid_plan_invocation"}
        if (request.tool_version != plan_tool_versions.LEGACY_PLAN_EXECUTOR_VERSION
                and not (request.tool_version is None
                         and plan_tool_versions.LEGACY_PLAN_EXECUTOR_VERSION ==
                             plan_tool_versions.UNVERSIONED_LEGACY_COMPATIBLE_VERSION)):
            return {"ok": False, "error": "plan_tool_version_mismatch"}
        # An intervening claim/CAS change may appear after the AgentLoop's
        # coarse guard. Re-check immediately before durable journal begin.
        if execution_guard is not None and not execution_guard():
            return {"ok": False, "error": "plan_execution_guard_conflict"}
        dispatch_id = None
        if dispatch_journal is not None:
            if request.tool_version is None:
                dispatch_id = dispatch_journal.begin(
                    request.step_id, request.name, request.arguments)
            else:
                dispatch_id = dispatch_journal.begin(
                    request.step_id, request.name, request.arguments,
                    tool_version=request.tool_version)
        result = self._execute(
            request.name, request.arguments, [],
            clock, task_store=task_store, memory_store=memory_store,
            mcp_client=mcp_client)
        if dispatch_id is not None:
            dispatch_journal.record_receipt(dispatch_id, result)
        return result
