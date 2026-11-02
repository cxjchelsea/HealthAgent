"""Offline shadow classifier contract and fail-closed behavior."""
import asyncio
import json

import pytest

from evals.s25_04_route_shadow import shadow_classify


class Stub:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if isinstance(self.response, str):
            return self.response
        return json.dumps(self.response)


@pytest.mark.parametrize("label,request,saved", [
    ("ordinary_tool", "请创建一个待办", False),
    ("plan_control", "请只执行已保存计划的第一步", True),
    ("ambiguous", "先保存执行计划，再创建一个待办", False),
    ("ambiguous", "继续之前的步骤", True),
    ("ordinary_tool", "MCP 与 MCP 有什么不同？", False),
])
def test_shadow_schema_binding_and_contract(label, request, saved):
    stub = Stub({"label": label, "quote": request})
    result = asyncio.run(shadow_classify(stub, "test", request, saved))
    assert result == {"label": label, "reason": "bound"}
    system = stub.calls[0]["messages"][0]["content"]
    assert "ANY mixture" in system
    assert "saved_plan boolean" in system
    assert "NEVER authorizes" in system
    assert stub.calls[0]["response_schema"]["additionalProperties"] is False


@pytest.mark.parametrize("request,quote", [
    ("请创建一个待办", "保存计划"),
    ("MCP 和 MCP 的区别", "MCP"),
    ("查一下信息", ""),
])
def test_shadow_rejects_unbound_quote(request, quote):
    result = asyncio.run(shadow_classify(
        Stub({"label": "ordinary_tool", "quote": quote}), "test", request, False))
    assert result == {"label": "ambiguous", "reason": "unbound"}


@pytest.mark.parametrize("response", [
    {"label": "execute", "quote": "制定计划"},
    {"label": "ordinary_tool", "quote": "制定计划", "permission": True},
    "{bad json",
])
def test_shadow_rejects_invalid_schema(response):
    result = asyncio.run(shadow_classify(
        Stub(response), "test", "制定计划", True))
    assert result == {"label": "ambiguous", "reason": "invalid"}


def test_shadow_provider_error_fails_closed():
    result = asyncio.run(shadow_classify(
        Stub(error=RuntimeError("provider unavailable")), "test", "执行计划", True))
    assert result == {"label": "ambiguous", "reason": "invalid"}
