"""F03: pending MCP term clarification must use its own model schema."""
import asyncio
import json

from agent.pending_reply import classify_pending_reply


def test_pending_reply_uses_its_own_constrained_schema():
    class Provider:
        async def complete(self, *, messages, model, response_schema=None):
            assert response_schema is not None
            assert set(response_schema["properties"]) == {"kind"}
            assert "answer" in response_schema["properties"]["kind"]["enum"]
            assert messages[-1]["role"] == "user"
            context = json.loads(messages[-1]["content"])
            assert context["pending_tool"] == "mcp_reference_lookup"
            assert context["current_user_reply"] == "RAG"
            return '{"kind":"answer"}'

    assert asyncio.run(classify_pending_reply(
        Provider(), "strong",
        question="请告诉我需要查询的具体参考词条。",
        reply="RAG", tool="mcp_reference_lookup")) == "answer"


def test_pending_reply_explicit_diversion_never_resumes_tool():
    class Provider:
        async def complete(self, **kwargs):
            return '{"kind":"new_goal"}'

    assert asyncio.run(classify_pending_reply(
        Provider(), "strong",
        question="请告诉我需要查询的具体参考词条。",
        reply="改为创建一个任务", tool="mcp_reference_lookup")) == "new_goal"


def test_pending_reply_malformed_json_fails_closed():
    class Provider:
        async def complete(self, **kwargs):
            return '{"status":"understood"}'

    assert asyncio.run(classify_pending_reply(
        Provider(), "strong",
        question="请告诉我需要查询的具体参考词条。",
        reply="RAG", tool="mcp_reference_lookup")) == "uncertain"
