"""D06: reusable plan runner recovers equivalently, never conceals failures."""
import asyncio
from agent.loop import AgentLoop
from agent.plan_intent import PlanRequest, build_plan_checkpoint


class NoModel:
    async def decide(self, messages, tools):
        raise AssertionError("A bound plan continuation must not dispatch model decisions")


def plan(*expressions):
    steps=[{"goal":"计算 "+x,"expected_tool":"calculate",
            "expected_arguments":{"expression":x}} for x in expressions]
    return build_plan_checkpoint("计算 "+" ".join(expressions),PlanRequest(
        intent="plan_only",steps=steps))


def run(cp, scope="resume"):
    return asyncio.run(AgentLoop(NoModel()).run(
        "继续执行已有计划",plan_checkpoint=cp,
        plan_control=scope,execution_mode="read_only",include_evidence=True))


def test_two_step_resume_recovers_unsupported_power():
    result=run(plan("2+2","2**10"))
    assert result["status"] == "completed"
    assert [s["status"] for s in result["plan"]] == ["completed","completed"]
    assert result["plan_revision"] == 2
    assert any(x["event"] == "plan_step_recovered" for x in result["plan_events"])
    assert result["plan"][1]["expected_arguments"]["expression"] != "2**10"
    assert [x["observation"]["ok"] for x in result["audit"]] == [True,False,True]


def test_one_step_does_not_execute_later_step():
    result=run(plan("3+3","2**10"),"execute_first")
    assert result["status"] == "partial"
    assert [s["status"] for s in result["plan"]] == ["completed","pending"]
    assert len(result["audit"]) == 1


def test_nonrecoverable_division_by_zero_remains_pending():
    result=run(plan("4+4","9/0","3+3"))
    assert result["status"] == "partial"
    assert [s["status"] for s in result["plan"]] == ["completed","pending","pending"]
    assert result["plan_revision"] == 1
    assert len(result["audit"]) == 2


def test_unrelated_success_cannot_resolve_failed_step():
    result=run(plan("2**20"))
    assert result["status"] == "partial"
    assert result["plan"][0]["status"] == "pending"


def test_resume_checkpoint_does_not_repeat_completed():
    first=run(plan("2+2","2**10"),"execute_first")
    second=run(first["plan_checkpoint"])
    assert second["status"] == "completed"
    assert [x["observation"]["ok"] for x in second["audit"]] == [False, True]
