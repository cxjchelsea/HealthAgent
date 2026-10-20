"""S2-D live harness diagnostics without contacting an actual model provider."""
import asyncio

import httpx
import pytest

from agent.providers import ModelUnavailable
from evals.s2d_live_recovery import DiagnosticDecider


class ProviderFailure:
    async def decide(self, messages, tools):
        try:
            raise httpx.ConnectError("secret host must never be reported")
        except httpx.ConnectError as exc:
            raise ModelUnavailable("tool decision failed") from exc


class SuccessfulDecision:
    async def decide(self, messages, tools):
        return "ok"


def test_provider_error_records_safe_metadata_only():
    decider = DiagnosticDecider(ProviderFailure())
    with pytest.raises(ModelUnavailable):
        asyncio.run(decider.decide([], []))
    assert decider.requests == 1
    assert decider.failure == {
        "category": "network", "exception_type": "ConnectError"}
    assert decider.total_ms >= 0
    assert "secret" not in str(decider.failure)


def test_normal_model_decision_has_no_failure():
    decider = DiagnosticDecider(SuccessfulDecision())
    assert asyncio.run(decider.decide([], [])) == "ok"
    assert decider.requests == 1
    assert decider.failure is None
