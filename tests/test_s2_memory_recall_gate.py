"""Explicit saved-memory questions must not finish without a memory lookup."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class MemoryStub:
    def get(self, key):
        return {"ok": True, "found": True, "key": key, "value": "绿色"}


class NoRecall:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        return Decision("我没有你的测试偏好。", [])


class RetryThenRecall:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("不知道", [])
        if self.calls == 2:
            return Decision("", [{
                "id": "recall", "name": "recall_memory",
                "arguments": {"key": "测试偏好"}}])
        return Decision("你的测试偏好是绿色。", [])


def test_explicit_recall_never_claims_absence_without_observation():
    model = NoRecall()
    result = asyncio.run(AgentLoop(model, memory_store=MemoryStub()).run(
        "你还记得我的测试偏好吗？"))
    assert result["status"] == "partial"
    assert "尚未核实长期记忆" in result["answer"]
    assert "没有你的测试偏好" not in result["answer"]
    assert model.calls == 2
    assert any(t["type"] == "memory_recall_retry" for t in result["trace"])


def test_model_retries_recall_and_reports_observed_value():
    model = RetryThenRecall()
    result = asyncio.run(AgentLoop(model, memory_store=MemoryStub()).run(
        "你还记得我的测试偏好吗？"))
    assert result["status"] == "completed"
    assert "绿色" in result["answer"]
    assert model.calls == 3
    assert any(t.get("name") == "recall_memory" and t.get("ok")
               for t in result["trace"])


def test_unrelated_conversation_does_not_force_memory_lookup():
    model = NoRecall()
    result = asyncio.run(AgentLoop(model, memory_store=MemoryStub()).run(
        "请简单介绍一下 Agent。"))
    assert result["status"] == "completed"
    assert model.calls == 1
