"""D05-F02 model hallucination cannot turn execute-only text into a plan edit."""
import asyncio
import json
from agent.plan_revision_review import review_plan_revision
from agent.plan_intent import PlanRequest, build_plan_checkpoint


class Model:
    async def complete(self, *, model, messages, response_schema=None):
        return json.dumps({"intent":"revise","step_number":2,
                           "expected_tool":"calculate",
                           "old_argument":"8*8","new_argument":"9*9"})


def test_hallucinated_revision_not_admitted_without_old_new_in_current_request():
    cp=build_plan_checkpoint("查询 RAG，再计算 8*8",
        PlanRequest(intent="plan_only",steps=[
            {"goal":"查询 RAG","expected_tool":"mcp_reference_lookup",
             "expected_arguments":{"term":"RAG"}},
            {"goal":"计算 8*8","expected_tool":"calculate",
             "expected_arguments":{"expression":"8*8"}},
        ]))
    review=asyncio.run(review_plan_revision(
        Model(),"strong","现在请帮我执行第一步",cp))
    assert review.intent=="other"
