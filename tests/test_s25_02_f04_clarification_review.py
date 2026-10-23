"""F04: an explicit complete read-only action must not be suppressed by clarification."""
import asyncio
import json

import pytest

from agent.clarification_review import review_read_only_clarification
from agent.tools import execute_tool


@pytest.mark.parametrize("text_value, proposal", [
    ("请调用计算工具计算 12/(3-3)，并准确报告执行情况。", True),
    ("用计算工具求 9 除以 0；不能计算就解释原因。", True),
    ("请使用计算工具计算 1/0，如果失败请说明真实错误。", True),
    ("请查询 MCP 参考资料中的一个词条。", False),
    ("请计算一下", False),
    ("创建一个新任务", False),
    ("请解释除零为什么无定义，不要执行工具", False),
])
def test_independent_read_only_clarification_review(text_value, proposal):
    class Provider:
        async def complete(self, *, messages, model, response_schema=None):
            assert set(response_schema["properties"]) == {
                "complete_explicit_read_only_request"}
            payload = json.loads(messages[-1]["content"])
            assert payload["current_request"] == text_value
            assert "calculate" in payload["available_read_only_tools"]
            return json.dumps({"complete_explicit_read_only_request": proposal})
    assert asyncio.run(review_read_only_clarification(
        Provider(), "strong", text_value, "是否还需要其他信息？",
        ["calculate", "mcp_reference_lookup"])) is proposal


def test_unavailable_model_keeps_clarification():
    class Provider:
        async def complete(self, **kwargs):
            return "not-json"
    assert asyncio.run(review_read_only_clarification(
        Provider(), "strong", "请计算 12/(3-3)", "请澄清", ["calculate"])) is False


@pytest.mark.parametrize("expression", ["12/(3-3)", "1/0", "9/0"])
def test_calculation_failure_is_real_observation(expression):
    observed = execute_tool("calculate", {"expression": expression}, [])
    assert observed["ok"] is False
    assert observed["error"] == "division_by_zero"
    assert observed["retryable"] is False
