"""S25-05 M4 -> ordinary Invoker negative migration contract.

These tests deliberately prevent treating the ordinary read registry as a
drop-in saved-plan executor. They have NO effect on production routing.
"""
from agent.tool_invoker import OrdinaryReadInvoker, ReadInvocation
from agent.tool_registry import READ_REGISTRY, ReadToolRuntime, approved_read_grants
from agent.mcp_stdio_client import StdioMCPClient
from agent.tools import execute_tool


def test_legacy_plan_version_never_implicitly_maps_to_registry_version():
    assert READ_REGISTRY.version_for("calculate") == "1.0.0"
    result = OrdinaryReadInvoker().invoke(ReadInvocation(
        name="calculate", arguments={"expression": "19+23"},
        granted_names=("calculate",), exposed_names=("calculate",),
        expected_version="legacy-plan-v1"))
    assert result == {"ok": False, "error": "tool_version_mismatch"}


def test_saved_plan_route_cannot_borrow_ordinary_host_grants():
    for route in ("plan_execute_first", "plan_resume"):
        assert approved_read_grants(
            host_disposition="allow", host_route=route,
            host_mode="read_only", feature_enabled=True,
            calculate_registry_enabled=True,
            mcp_reference_registry_enabled=True) == ()


def test_mcp_legacy_call_and_registry_verified_call_are_not_interchangeable(monkeypatch):
    calls = []
    def legacy_call(self, name, arguments):
        calls.append(("legacy", name, dict(arguments)))
        return {"ok": True, "definition": "legacy", "source": "local"}
    def verified_call(self, arguments):
        calls.append(("verified", "mcp_reference_lookup", dict(arguments)))
        return {"ok": True, "definition": "verified", "source": "local"}
    monkeypatch.setattr(StdioMCPClient, "call", legacy_call)
    monkeypatch.setattr(StdioMCPClient, "call_trusted_reference", verified_call)
    client = StdioMCPClient()
    legacy = execute_tool(
        "mcp_reference_lookup", {"term": "RAG"}, [], mcp_client=client)
    assert legacy["definition"] == "legacy"
    assert calls == [("legacy", "mcp_reference_lookup", {"term": "RAG"})]
    calls.clear()
    registered = OrdinaryReadInvoker().invoke(ReadInvocation(
        name="mcp_reference_lookup", arguments={"term": "RAG"},
        granted_names=("mcp_reference_lookup",),
        exposed_names=("mcp_reference_lookup",),
        expected_version=READ_REGISTRY.version_for("mcp_reference_lookup")),
        runtime=ReadToolRuntime(mcp_client=client))
    assert registered["ok"] is True
    assert registered["definition"] == "verified"
    assert registered["tool_version"] == "1.0.0"
    assert calls == [("verified", "mcp_reference_lookup", {"term": "RAG"})]


def test_plan_version_does_not_authorize_ordinary_invoker_without_host_grant():
    result = OrdinaryReadInvoker().invoke(ReadInvocation(
        name="calculate", arguments={"expression": "19+23"},
        granted_names=(), exposed_names=("calculate",),
        expected_version="1.0.0"))
    assert result == {"ok": False, "error": "permission_denied"}
