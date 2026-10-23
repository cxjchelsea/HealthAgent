"""S25-01/02 missing_goal repair is narrow, verified and fail-closed."""
import asyncio
import json
import pytest

from agent.understanding import (
    UnderstandingService, UnderstandingContext, UnderstandingError
)


def incomplete():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": None,
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    })


class Model:
    def __init__(self, goal=None, faithful=True, response=None):
        self.calls = []
        self.goal = goal or {
            "objective": "用计算工具计算 2**10，失败时尝试等价乘法",
            "subgoals": [], "constraints": [], "success_criteria": []}
        self.faithful = faithful
        self.response = response or incomplete()

    async def complete(self, *, model, messages, response_schema=None):
        self.calls.append((model, messages, response_schema))
        if response_schema and "faithful" in str(response_schema):
            return json.dumps({"faithful": self.faithful})
        if response_schema and "GoalSpec" in str(response_schema.get("title", "")):
            return json.dumps(self.goal, ensure_ascii=False)
        return self.response


def ctx():
    return UnderstandingContext(current_message=
        "请使用计算工具计算 2**10，如不支持则调整为等价乘法。")


def test_three_missing_goal_attempts_use_verified_goal_repair():
    model = Model()
    result = asyncio.run(UnderstandingService(model, "light", "strong").understand(ctx()))
    assert result["result"].goal.objective.startswith("用计算工具")
    assert result["result"].change.kind == "new_goal"
    assert result["model_role"] == "strong"
    assert len(model.calls) == 5
    assert result["attempts"][-1]["diagnostic"] == "goal_repaired"


def test_unfaithful_goal_repair_is_not_accepted():
    model = Model(faithful=False)
    with pytest.raises(UnderstandingError) as failure:
        asyncio.run(UnderstandingService(model, "light", "strong").understand(ctx()))
    assert failure.value.code == "understanding_unavailable"
    assert failure.value.attempts[-1]["diagnostic"] == "goal_repair_unverified"


def test_invented_constraint_is_not_accepted_even_if_verifier_claims_true():
    goal = {"objective": "计算", "subgoals": [], "success_criteria": [],
            "constraints": [{"name": "save", "value": "yes",
                             "source": "inferred", "scope": "goal"}]}
    model = Model(goal=goal)
    with pytest.raises(UnderstandingError):
        asyncio.run(UnderstandingService(model, "light", "strong").understand(ctx()))


def test_non_missing_goal_failure_never_enters_goal_repair():
    model = Model(response="{}")
    with pytest.raises(UnderstandingError):
        asyncio.run(UnderstandingService(model, "light", "strong").understand(ctx()))
    assert len(model.calls) == 3
