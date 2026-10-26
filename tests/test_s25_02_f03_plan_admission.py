"""F03 plan admission separates underspecified MCP lookup from saved plans."""
import asyncio
import json

import pytest

from agent.plan_control import review_plan_creation_reference


@pytest.mark.parametrize("utterance, expected", [
    ("请查询 MCP 参考资料中的一个词条。", False),
    ("查一个参考词条，但我还没说词条是什么", False),
    ("查询 RAG 是什么", False),
    ("请使用计算工具计算 (19+23)*4。", False),
    ("帮我拟定三项工作任务，只给草案不要保存", False),
    ("创建一个任务叫测试报告", False),
    ("建立一个以后执行的正式计划：先查询 RAG，再计算 8*8；不要执行", True),
    ("创建一个可持久化的两步执行计划，稍后按步运行", True),
])
def test_plan_creation_semantic_gate(utterance, expected):
    class FakeProvider:
        async def complete(self, **kwargs):
            return json.dumps({"explicitly_requests_saved_plan": expected})
    assert asyncio.run(review_plan_creation_reference(
        FakeProvider(), "strong", utterance)) is expected


def test_plan_creation_review_invalid_fails_closed():
    class FakeProvider:
        async def complete(self, **kwargs):
            return "bad-json"
    assert asyncio.run(review_plan_creation_reference(
        FakeProvider(), "strong", "建立执行计划")) is False
