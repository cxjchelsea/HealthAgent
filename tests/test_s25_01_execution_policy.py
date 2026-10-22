"""Execution routing is separate from the understanding model schema."""
import asyncio
import json
from agent.execution_policy import decide_execution_policy, ExecutionPolicyDecision
from agent.understanding import UnderstandingResult


class FakePolicyProvider:
    def __init__(self, content):
        self.content = content
        self.schema = None
        self.messages = None
    async def complete(self, *, messages, model, response_schema=None):
        self.schema = response_schema
        self.messages = messages
        return self.content


def test_model_policy_has_independent_small_schema():
    assert "execution_intent" not in UnderstandingResult.model_json_schema()["properties"]
    model = FakePolicyProvider(json.dumps({"mode": "respond_only"}))
    decision = asyncio.run(decide_execution_policy(
        model, "strong", "写三项草案但不保存",
        {"status": "understood", "goal": {"objective": "草案"}}))
    assert decision == "respond_only"
    assert "mode" in model.schema["properties"]
    assert "goal" not in model.schema["properties"]


def test_policy_rejects_invalid_or_unexpected_authority():
    from agent.understanding import UnderstandingError
    import pytest
    model = FakePolicyProvider(json.dumps({"mode": "admin"}))
    with pytest.raises(UnderstandingError, match="execution_policy_invalid"):
        asyncio.run(decide_execution_policy(model, "strong", "test", {}))


def test_policy_accepts_only_bounded_modes():
    import pytest
    for mode in ("respond_only", "read_only", "execute"):
        assert ExecutionPolicyDecision.model_validate({"mode": mode}).mode == mode
    with pytest.raises(ValueError):
        ExecutionPolicyDecision.model_validate({"mode": "admin"})
