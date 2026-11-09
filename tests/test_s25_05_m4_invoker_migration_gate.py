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


def test_plan_read_invoker_dispatch_lifecycle_and_all_supported_read_tools():
    from agent.plan_read_invoker import PlanReadInvoker, PlanReadRequest
    from agent.plan_intent import READ_TOOLS

    calls = []

    class Journal:
        def begin(self, step_id, name, arguments, *, tool_version=None):
            calls.append(("begin", step_id, name, tool_version))
            return "dispatch-123"

        def record_receipt(self, dispatch_id, result):
            calls.append(("receipt", dispatch_id, result["ok"]))

    def execute(name, arguments, tasks, clock=None, **kwargs):
        calls.append(("execute", name))
        return {"ok": True, "value": 42}

    arguments = {
        "calculate": {"expression": "19+23"},
        "mcp_reference_lookup": {"term": "rag"},
        "utc_time": {}, "list_tasks": {}, "recall_memory": {"key": "qa"},
    }
    assert set(arguments) == set(READ_TOOLS)
    invoker = PlanReadInvoker(execute=execute)
    for name, args in arguments.items():
        calls.clear()
        result = invoker.invoke(
            PlanReadRequest(1, name, args, "legacy-plan-v1"),
            execution_guard=lambda: calls.append(("guard",)) or True,
            dispatch_journal=Journal())
        assert result == {"ok": True, "value": 42}
        assert calls == [
            ("guard",),
            ("begin", 1, name, "legacy-plan-v1"),
            ("execute", name),
            ("receipt", "dispatch-123", True),
        ]


def test_plan_read_invoker_rejects_without_dispatch_or_external_call(monkeypatch):
    from agent import plan_tool_versions as versions
    from agent.plan_read_invoker import PlanReadInvoker, PlanReadRequest

    seen = []
    class Journal:
        def begin(self, *args, **kwargs):
            seen.append("dispatch")
            raise AssertionError("invalid plan must not begin a journal")
    def execute(*args, **kwargs):
        seen.append("execute")
        raise AssertionError("invalid plan must not execute")
    invoker = PlanReadInvoker(execute=execute)
    invalid = [
        PlanReadRequest(1, "create_task", {"title": "unauthorized"}, "legacy-plan-v1"),
        PlanReadRequest(1, "calculate", {"expression": "19+23"}, "1.0.0"),
        PlanReadRequest(0, "calculate", {"expression": "19+23"}, "legacy-plan-v1"),
    ]
    for request in invalid:
        result = invoker.invoke(request, execution_guard=lambda: True,
                                dispatch_journal=Journal())
        assert result["ok"] is False
    blocked = invoker.invoke(
        PlanReadRequest(1, "calculate", {"expression": "19+23"},
                        "legacy-plan-v1"),
        execution_guard=lambda: False, dispatch_journal=Journal())
    assert blocked == {"ok": False, "error": "plan_execution_guard_conflict"}
    monkeypatch.setattr(versions, "LEGACY_PLAN_EXECUTOR_VERSION", "legacy-plan-v2")
    legacy = invoker.invoke(
        PlanReadRequest(1, "calculate", {"expression": "19+23"}, None),
        execution_guard=lambda: True, dispatch_journal=Journal())
    assert legacy["error"] == "plan_tool_version_mismatch"
    assert seen == []


def test_plan_read_invoker_schema_denials_do_not_begin_m4_journal():
    from agent.plan_read_invoker import PlanReadInvoker, PlanReadRequest

    dispatched = []
    class Journal:
        def begin(self, *args, **kwargs):
            dispatched.append("begin")
            raise AssertionError("malformed arguments must never be journaled")
    def execute(*args, **kwargs):
        dispatched.append("execute")
        raise AssertionError("malformed arguments must never execute")

    invoker = PlanReadInvoker(execute=execute)
    cases = (
        ("calculate", {"expression": "19+23", "extra": "not allowed"}),
        ("calculate", {"expression": ""}),
        ("mcp_reference_lookup", {"term": "RAG", "command": "rm -rf /"}),
        ("recall_memory", {"key": ""}),
        ("utc_time", {"surprise": "field"}),
        ("list_tasks", {"surprise": "field"}),
    )
    for name, args in cases:
        result = invoker.invoke(
            PlanReadRequest(1, name, args, "legacy-plan-v1"),
            execution_guard=lambda: True, dispatch_journal=Journal())
        assert result == {"ok": False, "error": "invalid_arguments"}
    assert dispatched == []
