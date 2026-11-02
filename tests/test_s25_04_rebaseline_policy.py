"""S25-04 rebaseline regression: never widen S25-02 execution-mode fence."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class ObserveTools:
    def __init__(self):
        self.visible = []

    async def decide(self, messages, tools):
        self.visible.append({item["function"]["name"] for item in tools})
        return Decision("No action necessary.", [])


def test_semantic_respond_only_never_exposes_tools():
    probe = ObserveTools()
    result = asyncio.run(AgentLoop(probe, skill_strategy="semantic").run(
        "Explain this sentence", execution_mode="respond_only",
    ))
    assert result["status"] == "completed"
    assert probe.visible == [set()]


def test_semantic_read_only_never_exposes_write_or_plan_actions():
    probe = ObserveTools()
    result = asyncio.run(AgentLoop(probe, skill_strategy="semantic").run(
        "Please check existing records", execution_mode="read_only",
    ))
    assert result["status"] == "completed"
    assert probe.visible
    assert "list_tasks" in probe.visible[0]
    assert "request_clarification" in probe.visible[0]
    assert not (probe.visible[0] & {
        "create_task", "save_memory", "propose_plan",
        "revise_plan", "complete_plan_step",
    })


def test_semantic_default_never_adds_write_capability():
    probe = ObserveTools()
    result = asyncio.run(AgentLoop(probe, skill_strategy="semantic").run(
        "Create a task", execution_mode="execute",
    ))
    assert result["status"] == "completed"
    assert "create_task" not in probe.visible[0]
    assert "save_memory" not in probe.visible[0]
