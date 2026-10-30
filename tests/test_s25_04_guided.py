"""S25-04 guided A/B experiment never broadens Host authority."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop
from agent.skills import SEMANTIC_GUIDANCE, select_tool_exposure
from agent.tools import TOOL_SCHEMAS


class Probe:
    def __init__(self):
        self.tools = []
        self.messages = []

    async def decide(self, messages, tools):
        self.tools.append({x["function"]["name"] for x in tools})
        self.messages.append(messages)
        return Decision("No external verification performed.", [])


def test_guided_exposure_matches_semantic_and_never_adds_writes():
    for goal in ("Please calculate the rate", "Check tasks", "Explain concepts"):
        standard = select_tool_exposure(goal, TOOL_SCHEMAS, strategy="semantic")
        guided = select_tool_exposure(goal, TOOL_SCHEMAS, strategy="semantic_guided")
        assert set(standard.tool_names) == set(guided.tool_names)
        assert "create_task" not in guided.tool_names
        assert "save_memory" not in guided.tool_names


def test_guided_prompt_distinct_and_read_only_host_fenced():
    probe = Probe()
    asyncio.run(AgentLoop(probe, skill_strategy="semantic_guided").run(
        "Please verify my completion percentage", execution_mode="read_only"))
    assert len(probe.tools) == 1
    assert "calculate" in probe.tools[0]
    assert not (probe.tools[0] & {"create_task", "save_memory", "propose_plan"})
    assert SEMANTIC_GUIDANCE in probe.messages[0][0]["content"]


def test_guided_respond_only_cannot_expose_any_tool():
    probe = Probe()
    asyncio.run(AgentLoop(probe, skill_strategy="semantic_guided").run(
        "Explain an idea", execution_mode="respond_only"))
    assert probe.tools == [set()]
