"""Provider matrix case isolation, without any live provider calls."""
from evals.s2d_provider_compatibility import CASES, build_cases
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

def test_fixed_request_controls_only_change_user_goal():
    cases = list(build_cases())
    assert len(cases) == 7
    fixed = cases[4:]
    assert [c[0] for c in fixed] == [
        "fixed_simple", "fixed_failure", "fixed_original"]
    assert len({c[3] for c in fixed}) == 1
    assert all(c[2] == ("propose_plan", "calculate", "revise_plan") for c in fixed)
    assert all(c[4] == "fixed_request" for c in fixed)
    assert fixed[-1][1] == CASES[-1][1]
    assert fixed[-1][3] == cases[3][3]
