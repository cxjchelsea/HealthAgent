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

def test_parallel_independent_actions_are_valid_if_state_matches():
    case = next(c for c in load_cases() if c["id"] == "S2A-011")
    result = {"status": "completed", "answer": "完成", "tasks": ["读书"],
              "audit": [
                  {"step": 1, "name": "create_task", "arguments": {"title": "读书"},
                   "observation": {"ok": True, "title": "读书"}},
                  {"step": 1, "name": "list_tasks", "arguments": {},
                   "observation": {"ok": True, "tasks": [{"title": "读书"}]}}]}
    checks = grade(case, result)
    assert "post_observation_decision" not in checks
    assert all(checks.values())

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


def test_parallel_calls_cannot_substitute_for_required_observation():
    case = next(c for c in load_cases() if c["id"] == "S2A-015")
    result = {"status": "completed", "answer": "12", "tasks": ["登记12"],
              "audit": [
                  {"step": 1, "name": "calculate", "arguments": {"expression": "3+9"},
                   "observation": {"ok": True, "value": 12}},
                  {"step": 1, "name": "create_task", "arguments": {"title": "登记12"},
                   "observation": {"ok": True, "title": "登记12"}},
                  {"step": 1, "name": "list_tasks", "arguments": {},
                   "observation": {"ok": True, "tasks": [{"title": "登记12"}]}}]}
    assert grade(case, result)["post_observation_decision"] is False


def test_record_time_accepts_descriptive_title_but_rejects_wrong_state():
    case = next(c for c in load_cases() if c["id"] == "S2A-016")
    audit = [
        {"step": 1, "name": "utc_time", "arguments": {},
         "observation": {"ok": True, "utc": "2026-10-19T00:00:00+00:00"}},
        {"step": 2, "name": "create_task",
         "arguments": {"title": "记录UTC时间：2026-10-19"},
         "observation": {"ok": True, "title": "记录UTC时间：2026-10-19"}},
    ]
    good = {"status": "completed", "answer": "任务已创建",
            "tasks": ["记录UTC时间：2026-10-19"], "audit": audit}
    assert all(grade(case, good).values())
    bad = {**good, "tasks": ["其它任务"]}
    assert grade(case, bad)["task_title_and_state"] is False
