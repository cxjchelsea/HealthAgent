"""Real-user B02/B03 reproduction using deterministic model-decider proposals."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


def structured():
    return {"schema_version": "s25-01-v1", "status": "understood",
            "goal": {"objective": "test", "subgoals": [],
                     "constraints": [], "success_criteria": []},
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None}}


class Choose:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments

    async def decide(self, messages, tools):
        return Decision("", [{"id": "call", "name": self.name,
                              "arguments": self.arguments}])


def execute(text, name, args, **extra):
    return asyncio.run(AgentLoop(Choose(name, args), **extra).run(
        text, understanding_result=structured(),
        execution_mode="read_only" if name == "mcp_reference_lookup" else "execute",
        include_evidence=True))


def test_b02_unspecified_reference_term_never_invokes_mcp():
    class MCP:
        def call(self, name, args):
            raise AssertionError("A missing user term must not reach MCP")
    result = execute("请查询 MCP 参考资料中的一个词条。", "mcp_reference_lookup",
                     {"term": "rag"}, mcp_client=MCP())
    assert result["status"] == "partial"
    assert result["termination_reason"] == "needs_input"
    assert "词条" in result["answer"]
    assert not result["audit"]


def test_b03_unnamed_task_never_creates_persistent_record():
    class Store:
        def create(self, title):
            raise AssertionError("A guessed title must never be persisted")
    result = execute("帮我创建一个任务。", "create_task",
                     {"title": "示例任务"}, task_store=Store())
    assert result["status"] == "partial"
    assert result["termination_reason"] == "needs_input"
    assert "任务名称" in result["answer"]
    assert not result["audit"]


def test_explicit_reference_term_is_permitted():
    class MCP:
        def call(self, name, args):
            assert args == {"term": "rag"}
            return {"ok": True, "definition": "retrieval augmented generation"}
    result = execute("请查询词条 RAG 的定义", "mcp_reference_lookup",
                     {"term": "rag"}, mcp_client=MCP())
    assert any(item["observation"]["ok"] for item in result["audit"])


def test_explicit_task_title_is_permitted():
    class Store:
        def create(self, title):
            assert title == "复习 Python"
            return {"ok": True, "title": title, "task_id": "test"}
    result = execute("创建名为复习 Python的任务", "create_task",
                     {"title": "复习 Python"}, task_store=Store())
    assert any(item["observation"]["ok"] for item in result["audit"])


def test_mixed_batch_missing_write_argument_blocks_all_actions():
    class Mixed:
        async def decide(self, messages, tools):
            return Decision("", [
                {"id": "first", "name": "calculate", "arguments": {"expression": "2+2"}},
                {"id": "second", "name": "create_task",
                 "arguments": {"title": "臆造标题"}}])
    result = asyncio.run(AgentLoop(Mixed()).run(
        "计算 2+2 然后创建一个任务", understanding_result=structured(),
        execution_mode="execute", include_evidence=True))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "needs_input"
    assert not result["audit"]
