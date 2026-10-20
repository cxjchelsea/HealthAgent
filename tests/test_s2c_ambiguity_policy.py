"""S2-C precedence handling guard independent of live provider."""
from pathlib import Path
from evals.s2c_live_benchmark import grade

POLICY = (Path(__file__).parents[1] / "agent" / "prompts" / "s2a_core_v1.md").read_text(encoding="utf-8")


def test_standard_precedence_policy():
    assert "conventional arithmetic precedence" in POLICY
    assert "Ask for clarification only if critical information" in POLICY


def test_natural_precedence_grading():
    outcome = {"status": "completed", "answer": "90",
               "audit": [{"name": "calculate",
                          "arguments": {"expression": "18+24*3"},
                          "observation": {"ok": True, "value": 90}}]}
    assert all(grade("math_natural_precedence", outcome).values())
