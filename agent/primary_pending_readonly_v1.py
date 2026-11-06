"""Governed v1 read-only pending continuation.

The user reply only supplies a missing tool argument. It never supplies fresh
authorization, tool choice, or permission to execute a write. Host ownership of
the prior pending Turn is required by the caller. The durable attempt is
prepared BEFORE dispatch and recorded AFTER observation. A prepared attempt
means unknown outcome, even for a read, and is not automatically retried.
"""
from agent.tools import SCHEMAS, execute_tool
from agent.mcp_stdio_client import StdioMCPClient
from services.evidence_store import (
    AttemptEvidenceStore, bounded_projection, make_attempt,
)


def execute_pending_readonly_v1(*, message: str, conversation_id: str,
                                turn_id: str, session_factory,
                                prior_pending_action: str,
                                prior_origin_verified: bool,
                                mcp_client_factory=StdioMCPClient) -> dict:
    trace = [{"step": 0, "type": "primary_pending_readonly_v1"}]

    def deny(reason):
        return {"run_id": turn_id, "status": "partial",
                "termination_reason": reason,
                "answer": "待补充参数没有通过安全验证，未调用查询工具。",
                "trace": trace, "steps": 0}

    if not prior_origin_verified or prior_pending_action != "mcp_reference_lookup":
        return deny("primary_v1_pending_origin_denied")
    value = message.strip()
    try:
        arguments = SCHEMAS["mcp_reference_lookup"].model_validate(
            {"term": value}).model_dump()
    except ValueError:
        return deny("primary_v1_pending_argument_invalid")

    journal = AttemptEvidenceStore(conversation_id, turn_id)
    if journal.unresolved(session_factory):
        # Conservative single-conversation fence. A prepared receipt is unknown.
        return deny("primary_v1_pending_reconciliation_required")
    attempt = make_attempt(turn_id, "mcp_reference_lookup", arguments,
                           {"ok": False, "error": "prepared"})
    journal.begin(session_factory, attempt)
    # Never retry after begin if the tool or receipt fails: leave prepared
    # evidence for reconciliation. This path has no write permissions.
    observed = execute_tool(
        "mcp_reference_lookup", arguments, [],
        mcp_client=mcp_client_factory())
    projection = bounded_projection("mcp_reference_lookup", observed)
    attempt["status"] = "succeeded" if observed.get("ok") is True else "failed"
    attempt["error_code"] = (
        None if observed.get("ok") is True
        else str(observed.get("error", "unknown"))[:80])
    attempt["result_projection"] = projection
    journal.finish(session_factory, attempt)

    success = observed.get("ok") is True
    summary = "已查询词条：" + value
    if success:
        detail = projection.get("definition", projection.get("value", ""))
        if isinstance(detail, str) and detail:
            summary += "。" + detail[:1000]
    else:
        summary = "词条查询未成功；没有自动重试，请确认后重新发起请求。"
    return {"run_id": turn_id,
            "status": "completed" if success else "partial",
            "termination_reason": (
                "verified_clarification_execution" if success
                else "clarification_tool_failed"),
            "answer": summary,
            "trace": trace + [{"step": 1, "type": "clarification_resumed",
                               "name": "mcp_reference_lookup", "ok": success}],
            "steps": 1}
