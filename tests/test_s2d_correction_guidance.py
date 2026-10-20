"""S2-D model-facing goal correction guidance must remain available."""
from agent.skills import load_instructions


def test_planning_skill_explains_completed_step_invalidation():
    instructions = load_instructions(
        "更正刚才的目标：不是8*8，而是9*9。请重新修订计划。",
        strategy="dynamic", max_chars=2000)
    assert "invalidate_step_ids" in instructions
    assert "completed" in instructions
    assert "preserve others" in instructions
