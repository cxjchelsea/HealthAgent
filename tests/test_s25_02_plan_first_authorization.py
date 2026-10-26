"""Focused regression for second real-Qwen D03 first-step authorization."""
import asyncio
import json

from agent.plan_execution_review import review_plan_execution_authorization
from agent.planning import PlanLedger


def checkpoint():
    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
         "expected_arguments": {"term": "RAG"}},
        {"goal": "计算 9*9", "expected_tool": "calculate",
         "expected_arguments": {"expression": "9*9"}},
    ])["ok"]
    return ledger.checkpoint()


class ScopeAware:
    def __init__(self):
        self.calls = 0

    async def complete(self, *, model, messages, response_schema=None):
        self.calls += 1
        prompt = messages[0]["content"]
        data = json.loads(messages[-1]["content"])
        assert "ONLY the first saved step" in prompt
        assert data["requested_plan_action"] == "execute_first"
        assert data["pending_step_count"] == 2
        assert data["pending_step_goals"] == ["查询 RAG", "计算 9*9"]
        return json.dumps({"explicitly_authorizes_pending_steps": True})


def test_first_step_explicit_scope_authorized_without_other_steps():
    provider = ScopeAware()
    assert asyncio.run(review_plan_execution_authorization(
        provider, "strong", "现在只执行已保存计划的第一步，其余步骤先不执行。",
        checkpoint(), "execute_first"))
    assert provider.calls == 1


def test_independent_calculation_negative_veto_before_model():
    class NoModel:
        async def complete(self, **kwargs):
            raise AssertionError("must not call model for explicit veto")

    assert not asyncio.run(review_plan_execution_authorization(
        NoModel(), "strong",
        "请单独使用计算工具计算 19+23，不要执行刚才的计划。",
        checkpoint(), "execute_first"))


def test_missing_checkpoint_cannot_authorize_saved_plan():
    class NoModel:
        async def complete(self, **kwargs):
            raise AssertionError("must not call model for invalid checkpoint")

    assert not asyncio.run(review_plan_execution_authorization(
        NoModel(), "strong", "现在只执行已保存计划的第一步，其余步骤先不执行。",
        {}, "execute_first"))
