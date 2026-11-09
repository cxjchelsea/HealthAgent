"""S25-05 Step 5: Host-admitted read observations are not model prose."""
import asyncio
from datetime import datetime, timezone

from agent.decision import Decision
from agent.loop import AgentLoop


def test_host_read_only_no_tool_decision_retries_once_then_observes():
    class Model:
        def __init__(self):
            self.calls = 0

        async def decide(self, messages, tools):
            self.calls += 1
            exposed = {x["function"]["name"] for x in tools}
            assert "utc_time" in exposed
            if self.calls == 1:
                return Decision("这是我猜测的时间", [])
            if self.calls == 2:
                assert any("no tool observation exists yet" in x.get("content", "")
                           for x in messages if x.get("role") == "system")
                return Decision("", [{"id": "s25_05_once", "name": "utc_time",
                                      "arguments": {}}])
            return Decision("核实完毕", [])

    model = Model()
    result = asyncio.run(AgentLoop(
        model, skill_strategy="all", host_read_grants=("utc_time",),
        require_ordinary_read_evidence=True,
        clock=lambda: datetime(2026, 11, 9, tzinfo=timezone.utc)
    ).run("请读取当前 UTC 时间", execution_mode="read_only", include_evidence=True))
    assert model.calls == 3
    assert result["status"] == "completed"
    assert result["termination_reason"] == "model_final"
    assert sum(x.get("type") == "ordinary_read_retry" for x in result["trace"]) == 1
    assert any(x.get("type") == "tool" and x.get("name") == "utc_time"
               and x.get("ok") for x in result["trace"])


def test_host_read_only_without_tool_stays_unverified_after_one_retry():
    class Model:
        def __init__(self):
            self.calls = 0

        async def decide(self, messages, tools):
            self.calls += 1
            return Decision("我查询过了", [])

    model = Model()
    result = asyncio.run(AgentLoop(
        model, skill_strategy="all", host_read_grants=("list_tasks",),
        require_ordinary_read_evidence=True,
    ).run("查询当前任务清单", execution_mode="read_only"))
    assert model.calls == 2
    assert result["status"] == "partial"
    assert result["termination_reason"] == "ordinary_read_unverified"
    assert not any(x.get("type") == "tool" for x in result["trace"])


def test_ordinary_read_gate_off_keeps_legacy_decision_semantics():
    class Model:
        async def decide(self, messages, tools):
            return Decision("直接回复", [])

    result = asyncio.run(AgentLoop(
        Model(), skill_strategy="all",
        require_ordinary_read_evidence=False,
    ).run("解释概念", execution_mode="read_only"))
    assert result["status"] == "completed"


def test_saved_task_query_does_not_trigger_legacy_memory_early_return():
    """Regression for actual Qwen case: '已保存的待办' was mistaken for memory."""
    class Memory:
        def list(self):
            raise AssertionError("old memory substring shortcut must not run")

    class Tasks:
        def list(self):
            return {"ok": True, "tasks": [{"title": "S25-05-独立验收事项"}]}

    class Model:
        def __init__(self):
            self.calls = 0

        async def decide(self, messages, tools):
            self.calls += 1
            names = {x["function"]["name"] for x in tools}
            assert "list_tasks" in names
            if self.calls == 1:
                return Decision("", [{"id": "list_now", "name": "list_tasks",
                                      "arguments": {}}])
            return Decision("已读取实际待办列表", [])

    model = Model()
    result = asyncio.run(AgentLoop(
        model, skill_strategy="all", memory_store=Memory(),
        task_store=Tasks(), host_read_grants=("list_tasks",),
        require_ordinary_read_evidence=True,
    ).run("请从当前会话任务列表工具查询已保存的待办事项。",
          execution_mode="read_only", include_evidence=True))
    assert model.calls == 2
    assert result["status"] == "completed"
    assert result["termination_reason"] == "model_final"
    assert any(x.get("type") == "tool" and x.get("name") == "list_tasks"
               and x.get("ok") is True for x in result["trace"])
    assert any(x["name"] == "list_tasks" for x in result["audit"])


def test_legacy_memory_shortcut_remains_available_for_compatible_caller():
    class Memory:
        def list(self):
            return [{"key": "qa-code", "value": "synthetic-value"}]

    class NeverCalled:
        async def decide(self, messages, tools):
            raise AssertionError("legacy exact memory lookup should short-circuit")

    result = asyncio.run(AgentLoop(
        NeverCalled(), memory_store=Memory(),
        require_ordinary_read_evidence=False,
    ).run("你之前保存的 qa-code 是什么？", execution_mode="read_only"))
    assert result["status"] == "completed"
    assert any(x.get("type") == "memory_index_lookup"
               for x in result["trace"])


def test_primary_reference_lookup_pending_marker_remains_restricted():
    """The MCP prompt refinement must not weaken the strict proposal schema."""
    import pytest
    from pydantic import ValidationError
    from agent.primary_route_v1 import RouteSignal, _PRIMARY_SYSTEM

    base = {
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool",
        "quote": "查询 RAG",
        "execution_scope": "none",
        "effect_intent": "read_only",
        "steps": [],
        "pending_read_tool_name": None,
    }
    valid = RouteSignal.model_validate(base)
    assert valid.route_kind == "ordinary_tool"
    with pytest.raises(ValidationError):
        RouteSignal.model_validate({
            **base, "pending_read_tool_name": "mcp_reference_lookup",
        })
    assert "MCP FIELD-CONSISTENCY CHECK" in _PRIMARY_SYSTEM
    assert "pending_read_tool_name=null" in _PRIMARY_SYSTEM
