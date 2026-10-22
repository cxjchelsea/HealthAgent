import asyncio
from datetime import datetime, timezone
from agent.decision import Decision
from agent.loop import AgentLoop
from agent.tools import execute_tool, safe_calculate

class ScriptedDecision:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.received = []
    async def decide(self, messages, tools):
        self.received.append(messages)
        return next(self.decisions)

def call(name, arguments, id="a"):
    return Decision("", [{"name": name, "arguments": arguments, "id": id}])

def test_arithmetic_is_sandboxed():
    assert safe_calculate("(3+5)*2") == 16
    assert not execute_tool("calculate", {"expression": "__import__('os').system('id')"}, [])["ok"]
    assert not execute_tool("calculate", {"expression": "9**999"}, [])["ok"]
    assert not execute_tool("calculate", {"expression": "1/0"}, [])["ok"]
    assert not execute_tool("shell", {}, [])["ok"]
    assert not execute_tool("create_task", {"title": "x", "unexpected": "y"}, [])["ok"]

def test_real_multistep_tool_loop():
    model = ScriptedDecision([
        call("calculate", {"expression": "12*3"}),
        call("create_task", {"title": "核对36"}, "b"),
        call("list_tasks", {}, "c"),
        Decision("计算得36，已创建任务核对36。", []),
    ])
    result = asyncio.run(AgentLoop(model).run("计算12*3，把结果建立成任务，列出任务"))
    assert result["status"] == "completed"
    assert result["steps"] == 4
    assert [t["name"] for t in result["trace"] if t["type"] == "tool"] == ["calculate", "create_task", "list_tasks"]
    assert any("36" in m["content"] for m in model.received[1] if m["role"] == "tool")
    assert any("核对36" in m["content"] for m in model.received[-1] if m["role"] == "tool")

def test_step_budget_stops_model():
    model = ScriptedDecision([call("calculate", {"expression": "1+1"}) for _ in range(3)])
    result = asyncio.run(AgentLoop(model, max_steps=2).run("再算一次"))
    assert result["status"] == "step_limit"
    assert len([x for x in result["trace"] if x["type"] == "tool"]) == 1

def test_invalid_tool_observation_continues():
    model = ScriptedDecision([call("unknown", {}), Decision("这个工具不可用。", [])])
    result = asyncio.run(AgentLoop(model).run("试试看"))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"
    assert result["trace"][1]["ok"] is False

def test_utc_tool_injected_clock():
    t = datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert execute_tool("utc_time", {}, [], clock=lambda:t)["utc"].startswith("2026-10-19")
