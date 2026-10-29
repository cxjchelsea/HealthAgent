"""Verify M4 performance telemetry stays request-local."""
from fastapi.testclient import TestClient
from agent.config import Settings
from apps.api.main import create_app
from evals.s25_02_m4_paired_performance import summarize


def test_model_attempt_headers_on_fake_requests(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'perf.db'}",
                              model_provider="fake"))
    with TestClient(app) as client:
        for path in ("/healthz", "/model"):
            response = client.get(path)
            assert response.headers["x-healthagent-model-http-attempts"] == "0"
            assert response.headers["x-healthagent-shadow-http-attempts"] == "0"


def test_incomplete_model_attempts_not_summed_as_zero():
    cases = [{"functional_pass": True, "requests": [
        {"elapsed_ms": 100, "provider_http_attempts": 2,
         "shadow_http_attempts": 1, "response": {}},
        {"elapsed_ms": 200, "provider_http_attempts": None,
         "shadow_http_attempts": None, "response": {}}]}]
    metrics = summarize({}, cases)
    assert metrics["provider_http_attempts"]["total"] is None
    assert metrics["provider_http_attempts"]["observed"] == 1
    assert metrics["latency_ms"]["p50"] == 150
