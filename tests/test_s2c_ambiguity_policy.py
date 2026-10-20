"""S2-C ambiguity policy guard without substituting a simulated Qwen result."""
from pathlib import Path
from evals.s2c_live_benchmark import grade

POLICY = (Path(__file__).parents[1] / "agent" / "prompts" / "s2a_core_v1.md").read_text(encoding="utf-8")


def test_ambiguity_instruction_is_present_without_forcing_explicit_cases():
    assert "ask a concise clarification question" in POLICY
    assert "按通常运算优先级计算 18+24*3" in POLICY
    assert "(18+24)*3" in POLICY


def test_ambiguous_response_requires_question_and_no_calculate():
    outcome = {"status": "completed", "answer": "请确认是先算18+24还是先算24*3？",
               "audit": []}
    assert all(grade("math_ambiguous", outcome).values())
    not_asked = {"status": "completed", "answer": "可能是90或者126。", "audit": []}
    assert not grade("math_ambiguous", not_asked)["clarification_requested"]
