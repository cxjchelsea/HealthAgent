"""CC-03: mutually exclusive plan admission stays subject to Host evidence."""
import pytest

from agent.plan_control import resolve_plan_admission
from agent.plan_intent import PlanRequest


@pytest.mark.parametrize("intent,checkpoint,creation,missing,expected", [
    ("other", None, False, None, "ordinary"),
    ("other", {"steps":[]}, False, None, "ordinary"),
    ("plan_only", None, False, None, "ordinary"),
    ("plan_only", None, True, None, "create"),
    ("plan_only", {"steps":[]}, True, None, "existing_plan"),
    ("execute_first", None, False, None, "ordinary"),
    ("execute_first", None, False, False, "ordinary"),
    ("execute_first", None, False, True, "missing_plan"),
    ("resume", None, False, True, "missing_plan"),
    ("resume", None, False, False, "ordinary"),
    ("resume", {"steps":[]}, False, None, "resume"),
    ("execute_first", {"steps":[]}, False, None, "execute_first"),
])
def test_host_plan_admission_matrix(intent, checkpoint, creation, missing, expected):
    result = resolve_plan_admission(
        PlanRequest(intent=intent), existing_checkpoint=checkpoint,
        creation_confirmed=creation, missing_reference=missing)
    assert result.kind == expected


def test_plan_route_never_infers_new_permissions():
    assert resolve_plan_admission(
        PlanRequest(intent="plan_only", steps=[
            {"expected_tool":"create_task", "expected_arguments":{"title":"x"}, "goal":"x"}
        ]), existing_checkpoint=None, creation_confirmed=False).kind == "ordinary"
