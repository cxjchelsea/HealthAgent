"""Typed provider-response diagnostics without model network access."""
import asyncio
import httpx
import pytest
from agent.decision import NativeToolDecision, ToolDecisionError
from agent.config import Settings
from evals.s2d_live_recovery import DiagnosticDecider

class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload

class FakeClient:
    def __init__(self, payload): self.payload=payload
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def post(self, *args, **kwargs): return FakeResponse(self.payload)

@pytest.mark.parametrize(("payload", "expected"), [
    ({"message": {"content": ""}}, "empty_decision"),
    ({"message": {"tool_calls": [{"not_function": True}]}}, "malformed_tool_call"),
    ({"message": 13}, "invalid_response"),
    ({"error": "secret server body"}, "provider_rejected"),
    ([1, 2], "invalid_response"),
])
def test_typed_decision_failure_without_payload_leaks(monkeypatch, payload, expected):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient(payload))
    decider = DiagnosticDecider(NativeToolDecision(Settings(model_provider="ollama_proxy")))
    with pytest.raises(ToolDecisionError) as caught:
        asyncio.run(decider.decide([{"role":"user","content":"test"}], []))
    assert caught.value.code == expected
    assert decider.failure["reason_code"] == expected
    assert "secret server body" not in str(decider.failure)
