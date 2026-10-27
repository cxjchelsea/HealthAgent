"""D06 scope is independent, bounded and fail-closed."""
import asyncio
from agent.plan_execution_scope import review_plan_execution_scope

class Provider:
    def __init__(self, reply):
        self.reply=reply
    async def complete(self, **kwargs):
        return self.reply


def test_explicit_all_and_single_step_scopes():
    assert asyncio.run(review_plan_execution_scope(
        Provider('{"scope":"all"}'),"strong","执行整份计划")) == "all"
    assert asyncio.run(review_plan_execution_scope(
        Provider('{"scope":"one"}'),"strong","只执行第一步")) == "one"


def test_invalid_scope_never_authorizes_full_plan():
    assert asyncio.run(review_plan_execution_scope(
        Provider('{"scope":"anything"}'),"strong","随便继续")) == "unclear"
    assert asyncio.run(review_plan_execution_scope(
        Provider('not json'),"strong","继续")) == "unclear"


def test_recovery_strategy_is_general_integer_power_not_just_two_to_ten():
    from agent.plan_recovery import candidate_for_failure, validates_recovery
    from agent.tools import execute_tool
    for old in ("3**5", "4**3", "7**2"):
        proposal=candidate_for_failure("calculate",{"expression":old},
                                      "unsupported_expression")
        assert proposal is not None
        observed=execute_tool(proposal.tool,proposal.arguments,[])
        assert observed["ok"]
        assert validates_recovery({"expression":old},proposal,observed)
    assert candidate_for_failure("calculate",{"expression":"4/0"},
                                 "division_by_zero") is None
    assert candidate_for_failure("calculate",{"expression":"5**20"},
                                 "unsupported_expression") is None
