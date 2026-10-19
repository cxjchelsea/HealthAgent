import asyncio
from evals.s2a_benchmark import evaluate, load_cases

def test_offline_twenty_case_harness():
    assert len(load_cases()) == 20
    result = asyncio.run(evaluate(False))
    assert result["mode"] == "scripted_harness"
    assert result["passed"] == 20
    assert result["tool_match_rate"] == 1
    assert all("latency_ms" in item for item in result["results"])
