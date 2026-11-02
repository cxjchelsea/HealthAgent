"""S25-04 bounded regression for two identified real-provider failures.

These tests assert secure validation, not a successful understanding recovery.
"""
import pytest
from pydantic import ValidationError
from agent.understanding import UnderstandingResult


def test_missing_goal_is_not_accepted():
    data = {
        "status": "understood",
        "goal": None,
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    }
    with pytest.raises(ValidationError, match="missing_goal"):
        UnderstandingResult.model_validate(data)


def test_unverified_reference_not_a_successful_goal():
    """A structurally valid proposed ID is not proof of authorization.

    The service's trusted reference check must remain in place.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "agent" / "understanding.py").read_text(encoding="utf-8")
    assert 'if result.change.target_goal_id is not None:' in source
    assert 'raise UnderstandingError("unverified_goal_reference")' in source


def test_tool_observation_is_separate_from_final_model_text():
    """Tool completion alone cannot establish answer fidelity."""
    from agent.mock_mcp import MockMCPClient
    observed = MockMCPClient().call("mcp_reference_lookup", {"term": "MCP"})
    assert observed["ok"] is True
    assert observed["definition"]
    assert observed["source"]
    # The final answer still requires independent matching/review.

import asyncio
import json
from agent.understanding import UnderstandingContext, UnderstandingError, UnderstandingService


class _SequenceModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return json.dumps(next(self.responses))


def _understanding(goal_id=None):
    return {
        "status": "understood",
        "goal": {"objective": "查看当前待办"},
        "change": {"kind": "new_goal", "target_goal_id": goal_id, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    }


def test_strong_reference_rejection_then_valid_regeneration():
    model = _SequenceModel([_understanding("fabricated"), _understanding()])
    service = UnderstandingService(model, "strong", "strong")
    result = asyncio.run(service.understand(
        UnderstandingContext(current_message="查看当前待办"), use_strong=True))
    assert model.calls == 2
    assert result["result"].change.target_goal_id is None
    assert [x["outcome"] for x in result["attempts"]] == [
        "unverified_goal_reference", "valid"]


def test_repeated_unverified_goal_reference_stays_unavailable():
    model = _SequenceModel([_understanding("fabricated"), _understanding("fabricated")])
    service = UnderstandingService(model, "strong", "strong")
    with pytest.raises(UnderstandingError) as caught:
        asyncio.run(service.understand(
            UnderstandingContext(current_message="查看当前待办"), use_strong=True))
    assert caught.value.code == "understanding_unavailable"
    assert model.calls == 2
    assert all(a["outcome"] == "unverified_goal_reference"
               for a in caught.value.attempts)

def test_strong_clarification_mismatch_repairs_under_original_validator():
    invalid = _understanding()
    invalid["status"] = "clarification_required"
    model = _SequenceModel([invalid, _understanding()])
    service = UnderstandingService(model, "strong", "strong")
    output = asyncio.run(service.understand(
        UnderstandingContext(current_message="查询参考资料"), use_strong=True))
    assert model.calls == 2
    assert output["result"].status == "understood"
    assert output["attempts"][0]["diagnostic"] == "clarification_status_mismatch"


def test_strong_clarification_mismatch_twice_fails_closed():
    invalid = _understanding()
    invalid["status"] = "clarification_required"
    model = _SequenceModel([invalid, invalid])
    service = UnderstandingService(model, "strong", "strong")
    with pytest.raises(UnderstandingError) as caught:
        asyncio.run(service.understand(
            UnderstandingContext(current_message="查询参考资料"), use_strong=True))
    assert caught.value.code == "understanding_unavailable"
    assert model.calls == 2
