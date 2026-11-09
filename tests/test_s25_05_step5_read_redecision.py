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
