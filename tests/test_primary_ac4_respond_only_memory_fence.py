"""WP-B: respond_only is a Host boundary even for eager memory lookup.

The built-in memory shortcut executes before normal tool exposure, so it must
obey Host execution_mode rather than relying on model tool-call restrictions.
"""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class MemorySpy:
    def __init__(self):
        self.calls = 0

    def list(self):
        self.calls += 1
        return [{"key": "测试代号", "value": "private-value"}]


class AnswerDecider:
    async def decide(self, messages, tools):
        assert tools == []
        return Decision("我无法在回答模式下读取账户记忆。", [])


def test_respond_only_never_eager_reads_memory():
    store = MemorySpy()
    result = asyncio.run(AgentLoop(
        AnswerDecider(), memory_store=store,
        skill_strategy="dynamic",
    ).run("你还记得我的测试代号吗？", execution_mode="respond_only"))
    assert result["status"] == "completed"
    assert store.calls == 0
    assert "private-value" not in result["answer"]
    assert not any(t.get("type") == "memory_index_lookup"
                   for t in result["trace"])


def test_read_only_preserves_authorized_memory_lookup():
    store = MemorySpy()
    result = asyncio.run(AgentLoop(
        AnswerDecider(), memory_store=store,
        skill_strategy="dynamic",
    ).run("你还记得我的测试代号吗？", execution_mode="read_only"))
    assert result["status"] == "completed"
    assert store.calls == 1
    assert "测试代号" in result["answer"]
