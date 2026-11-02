"""Shadow route predictions never participate in API routing."""
import asyncio
import json

from evals.s25_04_route_shadow import shadow_classify


class Stub:
    def __init__(self, response):
        self.response = response
    async def complete(self, **kwargs):
        return json.dumps(self.response)


def test_shadow_binds_unique_quote():
    result = asyncio.run(shadow_classify(
        Stub({"label": "ordinary_tool", "quote": "创建一个待办"}),
        "test", "请创建一个待办", False))
    assert result == {"label": "ordinary_tool", "reason": "bound"}


def test_shadow_rejects_missing_and_repeated_quote():
    for request, quote in [("请创建一个待办", "保存计划"),
                           ("MCP 和 MCP 的区别", "MCP")]:
        result = asyncio.run(shadow_classify(
            Stub({"label": "ordinary_tool", "quote": quote}),
            "test", request, False))
        assert result == {"label": "ambiguous", "reason": "unbound"}


def test_shadow_rejects_invalid_schema():
    result = asyncio.run(shadow_classify(
        Stub({"label": "execute", "quote": "计划"}),
        "test", "制定计划", True))
    assert result["label"] == "ambiguous"
