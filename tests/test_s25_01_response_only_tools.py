"""S25-01 boundary: interpretation may remove but never grant tool authority."""
import asyncio
from agent.loop import AgentLoop
from agent.decision import Decision

class Decider:
    def __init__(self):
        self.tools_seen = None
    async def decide(self, messages, tools):
        self.tools_seen = tools
        return Decision(content="这是一份不保存的文字草案。", tool_calls=[])

def test_response_only_goals_are_not_offered_tools():
    decider = Decider()
    result = asyncio.run(AgentLoop(decider).run(
        "请给我三项草案，不要保存",
        understanding_result={"execution_intent": "respond_only",
                              "change": {"kind": "new_goal"}}))
    assert decider.tools_seen == []
    assert result["status"] == "completed"
    assert result["plan_revision"] == 0

def test_read_only_intent_does_not_strip_all_tools():
    decider = Decider()
    result = asyncio.run(AgentLoop(decider).run(
        "计算8*8", understanding_result={"execution_intent": "read_only",
                                    "change": {"kind": "new_goal"}}))
    assert any(x["function"]["name"] == "calculate" for x in decider.tools_seen)
    assert result["status"] == "completed"

def test_read_only_policy_excludes_mutating_and_plan_tools():
    decider = Decider()
    asyncio.run(AgentLoop(decider).run(
        "读取已有任务", understanding_result={"change": {"kind": "new_goal"}},
        execution_mode="read_only"))
    names = {tool["function"]["name"] for tool in decider.tools_seen}
    assert "list_tasks" in names
    assert "create_task" not in names
    assert "save_memory" not in names
    assert "propose_plan" not in names
    assert "revise_plan" not in names


def test_explicit_execution_mode_overrides_legacy_interpretation():
    decider = Decider()
    asyncio.run(AgentLoop(decider).run(
        "生成草案", understanding_result={"execution_intent": "execute"},
        execution_mode="respond_only"))
    assert decider.tools_seen == []


def test_response_only_with_existing_checkpoint_does_not_regain_plan_tools():
    from agent.planning import PlanLedger
    ledger = PlanLedger()
    ledger.propose(["已创建的执行计划"])
    decider = Decider()
    asyncio.run(AgentLoop(decider).run(
        "仅回复草案", understanding_result={"change": {"kind": "new_goal"}},
        plan_checkpoint=ledger.checkpoint(), execution_mode="respond_only"))
    assert decider.tools_seen == []
