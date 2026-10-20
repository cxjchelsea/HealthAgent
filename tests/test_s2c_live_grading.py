"""Scoring regression for S2-C arithmetic / ambiguity diagnostics."""
from agent.tools import safe_calculate
from evals.s2c_live_benchmark import CASES, arithmetic_evidence, grade


def outcome(expression=None, answer="", value=None):
    audit = []
    if expression is not None:
        audit.append({"name": "calculate", "arguments": {"expression": expression},
                      "observation": {"ok": True, "value": value}})
    return {"status": "completed", "answer": answer, "audit": audit}


def test_explicit_arithmetic_requires_correct_result_and_answer():
    assert all(grade("math_explicit", outcome("(18+24)*3", "126", 126)).values())
    checks = grade("math_explicit", outcome("18+24*3", "90", 90))
    assert not checks["correct_tool_result"]
    assert not checks["answer_contains_marker"]


def test_precedence_is_separately_graded():
    assert safe_calculate("18+24*3") == 90
    assert safe_calculate("(18+24)*3") == 126
    assert all(grade("math_precedence", outcome("18+24*3", "90", 90)).values())


def test_ambiguity_requires_clarification_without_tool():
    yes = grade("math_ambiguous", outcome(answer="需要确认括号：先算哪一步？"))
    assert all(yes.values())
    no = grade("math_ambiguous", outcome("18+24*3", "90", 90))
    assert not all(no.values())


def test_no_other_tool_observations_in_diagnostics():
    raw = [{"name": "calculate", "arguments": {"expression": "1+1"},
            "observation": {"ok": True, "value": 2, "secret": "do-not-report"}},
           {"name": "recall_memory", "arguments": {"key": "private"},
            "observation": {"ok": True, "value": "secret"}}]
    assert arithmetic_evidence(raw) == [
        {"expression": "1+1", "value": 2, "ok": True, "error_code": None}]
    assert len(CASES) == 10
