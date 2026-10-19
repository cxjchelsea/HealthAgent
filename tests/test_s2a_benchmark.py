import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop
from evals.s2a_benchmark import evaluate, grade, load_cases

def test_offline_twenty_contracts():
    result = asyncio.run(evaluate(False))
    assert result["passed"] == 20
    assert result["scoring_version"] == "s2a-evidence-v2"

def test_wrong_title_is_rejected():
    case = next(c for c in load_cases() if c["id"] == "S2A-011")
    result = {"status": "completed", "answer": "完成", "tasks": ["错误标题"],
              "audit": [
                  {"step": 1, "name": "create_task", "arguments": {"title": "错误标题"},
                   "observation": {"ok": True}},
                  {"step": 2, "name": "list_tasks", "arguments": {},
                   "observation": {"ok": True, "tasks": [{"title": "错误标题"}]}}]}
    assert not all(grade(case, result).values())

def test_parallel_tools_do_not_count_as_observation_replanning():
    case = next(c for c in load_cases() if c["id"] == "S2A-011")
    result = {"status": "completed", "answer": "完成", "tasks": ["读书"],
              "audit": [
                  {"step": 1, "name": "create_task", "arguments": {"title": "读书"},
                   "observation": {"ok": True}},
                  {"step": 1, "name": "list_tasks", "arguments": {},
                   "observation": {"ok": True, "tasks": [{"title": "读书"}]}}]}
    checks = grade(case, result)
    assert checks["post_observation_decision"] is False

def test_failing_tool_must_be_acknowledged():
    case = next(c for c in load_cases() if c["id"] == "S2A-018")
    result = {"status": "completed", "answer": "结果是 10", "tasks": [],
              "audit": [{"step": 1, "name": "calculate",
                         "arguments": {"expression": "10/0"},
                         "observation": {"ok": False, "error": "invalid_arguments"}}]}
    assert grade(case, result)["failure_acknowledged"] is False

class Minimal:
    async def decide(self, messages, tools):
        return Decision("完成", [])

def test_default_agent_api_result_has_no_evidence():
    result = asyncio.run(AgentLoop(Minimal()).run("你好"))
    assert "audit" not in result
    assert "tasks" not in result
