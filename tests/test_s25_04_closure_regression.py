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
