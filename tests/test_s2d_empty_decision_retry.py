"""Safe, bounded retries for empty provider decisions."""
import asyncio

import httpx
import pytest

from agent.config import Settings
from agent.decision import NativeToolDecision, ToolDecisionError
from evals.s2d_live_recovery import DiagnosticDecider


class Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


class Client:
    def __init__(self, responses):
        self.responses = responses
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return None
    async def post(self, *args, **kwargs):
        return Response(self.responses.pop(0))


def make_decider(monkeypatch, payloads):
    responses = list(payloads)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client(responses))
    return DiagnosticDecider(NativeToolDecision(Settings(model_provider="ollama_proxy"))), responses


def test_empty_first_response_retries_once_then_accepts_tool(monkeypatch):
    decider, remaining = make_decider(monkeypatch, [
        {"message": {"content": "", "thinking": "do not reveal"}},
        {"message": {"content": "", "tool_calls": [{
            "function": {"name": "calculate", "arguments": {"expression": "8*8"}}}]}}
    ])
    decision = asyncio.run(decider.decide([{"role": "user", "content": "calculate"}], []))
    assert decision.tool_calls[0]["name"] == "calculate"
    assert decider.failure is None
    assert remaining == []


def test_two_empty_responses_fail_closed_with_redacted_shape(monkeypatch):
    decider, remaining = make_decider(monkeypatch, [
        {"message": {"content": "", "thinking": "private reasoning"}, "done_reason": "stop"},
        {"message": {"content": "", "thinking": "more private reasoning"}, "done_reason": "stop"},
    ])
    with pytest.raises(ToolDecisionError) as exc:
        asyncio.run(decider.decide([{"role": "user", "content": "test"}], []))
    assert exc.value.code == "empty_decision"
    shape = decider.failure["response_shape"]
    assert shape == {
        "message_present": True, "content_chars": 0, "tool_call_count": 0,
        "thinking_present": True, "done_reason": "stop", "retry_count": 1}
    assert remaining == []
    assert "private reasoning" not in str(decider.failure)


def test_malformed_call_never_retried(monkeypatch):
    decider, remaining = make_decider(monkeypatch, [
        {"message": {"tool_calls": [{"name": "not a function"}]}},
        {"message": {"content": "would be dangerous fallback"}},
    ])
    with pytest.raises(ToolDecisionError) as exc:
        asyncio.run(decider.decide([{"role": "user", "content": "test"}], []))
    assert exc.value.code == "malformed_tool_call"
    assert len(remaining) == 1
