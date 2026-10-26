"""Control admission precedence independent of model wording."""
import pytest

from agent.control_admission import admit_clarification


@pytest.mark.parametrize("pending, unsupported, complete, outcome", [
    (None, None, True, "read_only"),
    (None, None, False, "clarify"),
    ("mcp_reference_lookup", None, True, "clarify"),
    ("create_task", None, True, "clarify"),
    (None, "shell", True, "unsupported"),
    ("mcp_reference_lookup", "shell", False, "unsupported"),
])
def test_host_clarification_decision(pending, unsupported, complete, outcome):
    decision = admit_clarification(
        missing_argument_action=pending,
        unsupported_name=unsupported,
        complete_read_only_request=complete)
    assert decision.kind == outcome
    assert decision.skip_persistence == (outcome == "read_only")
    assert decision.unsupported_name == (unsupported if outcome == "unsupported" else None)
