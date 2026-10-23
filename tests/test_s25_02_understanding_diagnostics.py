"""C02 failure intake: bounded redacted Understanding diagnostics, no model content leakage."""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.understanding import UnderstandingError, UnderstandingService
from apps.api.main import create_app


class NeverDecide:
    async def decide(self, messages, tools):
        raise AssertionError("Understanding failure must block AgentLoop")


def test_understanding_failure_exposes_only_allowlisted_attempts(tmp_path, monkeypatch):
    async def failure(self, context, *, use_strong=False):
        raise UnderstandingError("understanding_unavailable", attempts=[
            {"role": "light", "outcome": "structured_schema_error", "retry": 0,
             "duration_ms": 112, "diagnostic": "field:goal",
             "raw_text": "SECRET RAW MODEL OUTPUT"},
            {"role": "strong", "outcome": "structured_consistency_error", "retry": 1,
             "duration_ms": 198, "diagnostic": "missing_goal",
             "exception": "SECRET MODEL PROMPT"},
            {"role": "bad", "outcome": "SECRET PROVIDER OUTPUT",
             "diagnostic": "SECRET TOKEN"},
        ])
    monkeypatch.setattr(UnderstandingService, "understand", failure)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'diag.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=NeverDecide())) as client:
        cid = client.post("/conversations").json()["id"]
        res = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请用计算工具计算 2**10",
            "client_request_id": "diagnostics-case-001"})
        assert res.status_code == 503
        body = res.json()["detail"]
        assert body["code"] == "understanding_unavailable"
        assert body["decision_steps"] == 0
        assert body["understanding_attempts"] == [
            {"role": "light", "outcome": "structured_schema_error",
             "retry": 0, "duration_ms": 112, "diagnostic": "field:goal"},
            {"role": "strong", "outcome": "structured_consistency_error",
             "retry": 1, "duration_ms": 198, "diagnostic": "missing_goal"},
        ]
        assert "SECRET" not in res.text
        assert "2**10" not in res.text


def test_other_provider_errors_do_not_claim_understanding_attempts(tmp_path, monkeypatch):
    async def failure(self, context, *, use_strong=False):
        raise UnderstandingError("model_timeout", attempts=[
            {"role": "light", "outcome": "model_timeout"}])
    monkeypatch.setattr(UnderstandingService, "understand", failure)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'timeout.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=NeverDecide())) as client:
        cid = client.post("/conversations").json()["id"]
        res = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "测试", "client_request_id": "diagnostics-case-002"})
        assert res.status_code == 503
        assert res.json()["detail"]["code"] == "model_timeout"
        assert "understanding_attempts" not in res.json()["detail"]
