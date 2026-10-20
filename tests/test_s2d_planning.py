"""S2-D deterministic plan and recovery contract tests."""
from agent.planning import PlanLedger
from agent.skills import exposed_tools
from agent.tools import TOOL_SCHEMAS


def test_plan_revision_preserves_verified_progress():
    ledger = PlanLedger()
    assert ledger.propose(["look up term", "compute result"])["ok"]
    assert ledger.complete(1, "mcp_reference_lookup", None)["error"] == "missing_success_evidence"
    assert ledger.complete(1, "mcp_reference_lookup",
                           {"name": "mcp_reference_lookup", "ok": True})["ok"]
    assert ledger.revise(1, ["retry computation"], "tool input failed")["ok"]
    steps = ledger.snapshot()
    assert steps[0]["status"] == "completed"
    assert steps[0]["evidence_tool"] == "mcp_reference_lookup"
    assert steps[1]["goal"] == "retry computation"
    assert ledger.revision == 2
    assert ledger.revise(1, ["stale"], "wrong revision")["error"] == "stale_plan_revision"


def test_plan_security_negative_cases():
    ledger = PlanLedger()
    assert ledger.propose([])["error"] == "invalid_plan"
    assert ledger.propose(["ok"] * 9)["error"] == "invalid_plan"
    assert ledger.propose(["one"])["ok"]
    assert ledger.propose(["another"])["error"] == "plan_already_exists"
    assert ledger.complete(1, "calculate", {"name": "calculate", "ok": False})["error"] == "missing_success_evidence"
    assert ledger.complete(1, "calculate", {"name": "utc_time", "ok": True})["error"] == "evidence_tool_mismatch"
    assert ledger.complete(99, "calculate", {"name": "calculate", "ok": True})["error"] == "unknown_step"
    assert ledger.revise(1, [], "missing")["error"] == "invalid_revision"


def test_planning_tools_are_scoped_to_workflow_goals():
    picked = [s["function"]["name"] for s in exposed_tools(
        "请为多步任务制定计划，失败后重新规划", TOOL_SCHEMAS)]
    assert {"propose_plan", "revise_plan", "complete_plan_step"}.issubset(set(picked))
    assert "propose_plan" not in [
        s["function"]["name"] for s in exposed_tools("现在 UTC 时间", TOOL_SCHEMAS)]
