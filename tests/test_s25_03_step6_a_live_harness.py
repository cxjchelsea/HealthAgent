"""Offline guards for optional S25-03 real-provider HTTP acceptance."""
import pytest


def test_real_http_acceptance_rejects_fake_provider(monkeypatch):
    from evals.s25_03_step6_a_live import evaluate
    monkeypatch.delenv("RUN_S25_03_LIVE", raising=False)
    with pytest.raises(RuntimeError, match="RUN_S25_03_LIVE"):
        evaluate()
    monkeypatch.setenv("RUN_S25_03_LIVE", "1")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    with pytest.raises(RuntimeError, match="real model provider"):
        evaluate()


def test_real_http_acceptance_has_bounded_repeats(monkeypatch):
    from evals.s25_03_step6_a_live import evaluate
    monkeypatch.setenv("RUN_S25_03_LIVE", "1")
    with pytest.raises(ValueError, match="repeats"):
        evaluate(0)
    with pytest.raises(ValueError, match="repeats"):
        evaluate(6)
