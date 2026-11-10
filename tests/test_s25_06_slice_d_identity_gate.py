"""S25-06 real-provider identity gate: never permit anonymous demo HTTP."""
import pytest
from pydantic import ValidationError

from agent.config import Settings


@pytest.mark.parametrize("kwargs", [
    {"allow_demo_identity": True, "user_tokens": "devtoken:owner"},
    {"allow_demo_identity": False, "user_tokens": ""},
])
def test_real_provider_dependency_runner_rejects_demo_or_missing_user_tokens(kwargs):
    with pytest.raises(ValidationError, match="s25_06_requires_explicit_non_demo_identity"):
        Settings(
            model_provider="ollama_proxy",
            routing_profile="unified",
            s25_06_dependency_plan_enabled=True,
            **kwargs)


def test_real_provider_dependency_runner_accepts_explicit_dev_auth_for_tests():
    cfg = Settings(
        model_provider="ollama_proxy",
        routing_profile="unified",
        s25_06_dependency_plan_enabled=True,
        allow_demo_identity=False,
        user_tokens="devtoken:owner",
    )
    assert cfg.s25_06_dependency_plan_enabled is True
    assert cfg.allow_demo_identity is False


def test_existing_fake_model_contract_canary_keeps_fixture_compatibility():
    cfg = Settings(
        model_provider="fake",
        routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
    )
    assert cfg.s25_06_dependency_plan_enabled is True


def test_other_real_provider_features_do_not_change_default_auth_behavior():
    cfg = Settings(model_provider="ollama_proxy", routing_profile="unified")
    assert cfg.s25_06_dependency_plan_enabled is False
