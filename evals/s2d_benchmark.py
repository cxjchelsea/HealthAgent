"""S2-D replayable plan ledger evaluation; does not claim real-model success."""
import json
from pathlib import Path

from agent.planning import PlanLedger


def evaluate():
    ledger = PlanLedger()
    proposed = ledger.propose(["lookup", "calculate"])
    denial = ledger.complete(1, "mcp_reference_lookup", None)
    completed = ledger.complete(1, "mcp_reference_lookup",
                                {"name": "mcp_reference_lookup", "ok": True})
    ledger.observe("calculate", {"ok": False, "error": "invalid_arguments"})
    retry = ledger.revise(1, ["calculate with corrected arguments"], "invalid_arguments")
    stale = ledger.revise(1, ["discard"], "stale")
    finished = ledger.complete(2, "calculate", {"name": "calculate", "ok": True})
    tests = {
        "plan_created": proposed["ok"],
        "unverified_completion_blocked": denial["error"] == "missing_success_evidence",
        "verified_step_completed": completed["ok"],
        "partial_revision_preserved_step": retry["ok"] and ledger.steps[0]["goal"] == "lookup",
        "stale_revision_blocked": stale["error"] == "stale_plan_revision",
        "replanned_step_completed": finished["ok"],
        "terminal_steps_completed": all(x["status"] == "completed" for x in ledger.steps),
    }
    return {"suite": "s2d-ledger-replay-v1", "mode": "deterministic_no_llm",
            "checks": tests, "passed": all(tests.values()),
            "final_plan": ledger.snapshot(), "events": ledger.events,
            "limitations": ["In-memory per-run plan only", "No semantic verification of goal-to-tool evidence",
                            "No persisted cross-run plan or user correction reconciliation"]}


def main():
    result = evaluate()
    path = Path("evals/results/s2d/ledger-replay-v1.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("S2-D plan ledger:", "PASS" if result["passed"] else "FAIL")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
