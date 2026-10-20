"""Provider matrix case isolation, without any live provider calls."""
from evals.s2d_provider_compatibility import CASES
from agent.tools import TOOL_SCHEMAS

def test_cases_are_incremental_and_tool_names_are_allowlisted():
    assert [name for name, _, _ in CASES] == [
        "plain_text_no_tools", "calculate_only", "plan_and_calculate", "recovery_goal"]
    known = {t["function"]["name"] for t in TOOL_SCHEMAS}
    for _, goal, names in CASES:
        assert goal.strip()
        assert set(names).issubset(known)
    assert CASES[0][2] == ()
    assert CASES[1][2] == ("calculate",)
    assert "propose_plan" in CASES[2][2]
    assert "revise_plan" in CASES[3][2]
