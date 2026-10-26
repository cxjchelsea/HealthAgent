"""Revision review reports edit intent without a second semantic model call."""
import asyncio
import json

from agent.plan_revision_review import review_plan_revision
from agent.plan_intent import PlanRequest, build_plan_checkpoint


def checkpoint():
    return build_plan_checkpoint(
        "查询 RAG 再计算 8*8",
        PlanRequest(intent="plan_only", steps=[
            {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
             "expected_arguments": {"term": "RAG"}},
            {"goal": "计算 8*8", "expected_tool": "calculate",
             "expected_arguments": {"expression": "8*8"}},
        ]))


def test_ambiguous_edit_proposal_preserves_edit_signal():
    class Provider:
        async def complete(self, *, messages, model, response_schema=None):
            assert "edit_requested" in response_schema["properties"]
            return json.dumps({
                "intent": "other", "edit_requested": True,
                "step_number": None, "expected_tool": None,
                "old_argument": None, "new_argument": None,
            })
    proposal = asyncio.run(review_plan_revision(
        Provider(), "strong", "修改当前计划但先不确定步骤", checkpoint()))
    assert proposal.intent == "other"
    assert proposal.edit_requested is True


def test_non_edit_returns_explicit_negative():
    class Provider:
        async def complete(self, **kwargs):
            return '{"intent":"other","edit_requested":false}'
    proposal = asyncio.run(review_plan_revision(
        Provider(), "strong", "介绍一下 RAG", checkpoint()))
    assert proposal.intent == "other"
    assert proposal.edit_requested is False


def test_incomplete_revision_cannot_apply_even_when_edit_was_requested():
    class Provider:
        async def complete(self, **kwargs):
            return json.dumps({
                "intent":"revise", "edit_requested": True,
                "step_number":2, "expected_tool":"calculate",
                "old_argument":"8*8", "new_argument": "9*9",
            })
    proposal = asyncio.run(review_plan_revision(
        Provider(), "strong", "我想修改第二步", checkpoint()))
    assert proposal.intent == "other"
    assert proposal.edit_requested is True
