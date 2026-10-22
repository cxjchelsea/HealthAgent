import asyncio
import httpx
from agent.http_policy import model_http_trust_env
from agent.understanding import HTTPUnderstandingModel, UnderstandingResult

def test_local_ollama_ignores_system_proxy():
    for url in ("http://127.0.0.1:11434/api", "http://localhost:11434/api", "http://[::1]:11434/api"):
        assert model_http_trust_env("ollama_proxy", url) is False
    assert model_http_trust_env("ollama_proxy", "https://remote.example/api") is True
    assert model_http_trust_env("openai_compatible", "http://localhost:8080/v1") is True

def test_ollama_requests_actual_schema_and_disables_local_proxy(monkeypatch):
    captured = {}
    class FakeResponse:
        def raise_for_status(self): return None
        def json(self): return {"message": {"content": '{"status":"unresolved","goal":null,"change":{"kind":"chat"},"clarification":{"required":false}}'}}
    class FakeClient:
        def __init__(self, **kwargs): captured.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, json, headers):
            captured["url"] = url
            captured["format"] = json["format"]
            return FakeResponse()
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    model = HTTPUnderstandingModel("ollama_proxy", "http://127.0.0.1:11434/api", "", 30, 1024)
    asyncio.run(model.complete(messages=[{"role":"user", "content":"hi"}], model="qwen3:4b"))
    assert captured["trust_env"] is False
    assert captured["url"].endswith("/api/chat")
    assert captured["format"] == UnderstandingResult.model_json_schema()
