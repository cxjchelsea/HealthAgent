"""Explicit saved-memory questions must not finish without a memory lookup."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class MemoryStub:
    def list(self):
        return [{"key": "测试偏好", "value": "绿色"},
                {"key": "test_preference", "value": "蓝色"}]

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


def test_explicit_recall_reads_stored_value_without_model_choice():
    model = NoRecall()
    result = asyncio.run(AgentLoop(model, memory_store=MemoryStub()).run(
        "你还记得我的测试偏好吗？"))
    assert result["status"] == "completed"
    assert "测试偏好＝绿色" in result["answer"]
    assert "test_preference" in result["answer"]
    assert any(t["type"] == "memory_index_lookup" for t in result["trace"])
    assert model.calls == 0


def test_unknown_memory_key_returns_index_not_invented_value():
    model = NoRecall()
    result = asyncio.run(AgentLoop(model, memory_store=MemoryStub()).run(
        "你还记得我之前保存的旅行计划吗？"))
    assert "当前已保存的 Key" in result["answer"]
    assert "蓝色" not in result["answer"]
    assert model.calls == 0


def test_unrelated_conversation_does_not_force_memory_lookup():
    model = NoRecall()
    result = asyncio.run(AgentLoop(model, memory_store=MemoryStub()).run(
        "请简单介绍一下 Agent。"))
    assert result["status"] == "completed"
    assert model.calls == 1
