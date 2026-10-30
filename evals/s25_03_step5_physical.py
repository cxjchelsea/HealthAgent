"""S25-03 Step 5: independent read-only SQLite physical evidence acceptance.

Requires FULL 11-case real Qwen3 M5 live report from the exact local process.
Never replays tools, modifies database, or accepts synthetic/fake live reports.
"""
import argparse
import json
import sqlite3
from pathlib import Path

from evals.s25_02_m5_unified_physical import verify as verify_m5

EXPECTED = ("CREATE_A", "FIRST", "RESUME", "REPLAY", "CREATE_B",
            "REVISE", "CREATE_C", "CANCEL", "ORDINARY", "PENDING",
            "PENDING_ANSWER")


def verify(db_path: Path, report: dict) -> dict:
    # Preserve earlier M5 guards, including exact 11-case order, durable
    # claims, receipts, and replay checks. Never silently weaken them.
    prior = verify_m5(db_path, report)
    checks = []

    def check(label, ok):
        checks.append({"name": label, "result": "PASS" if ok else "FAIL"})

    if tuple(r.get("case") for r in report["records"]) != EXPECTED:
        raise ValueError("incomplete_or_out_of_order_mainline_report")
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        records = {r["case"]: r for r in report["records"]}
        conversations = report["conversations"]
        for table in ("agent_attempts", "agent_task_states",
                      "agent_state_transitions", "plan_tool_dispatches"):
            check(table + "_exists", db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone() is not None)
        for group in ("lifecycle", "revision", "cancel", "ordinary", "pending"):
            cid = conversations[group]
            state = db.execute(
                "SELECT revision,plan_revision,state,step_states "
                "FROM agent_task_states WHERE conversation_id=?", (cid,)).fetchall()
            if group in {"lifecycle", "revision", "cancel"}:
                # A cancelled plan has no persisted checkpoint, and the
                # projection must not claim its prior plan as executed.
                check(group + "_state_single", len(state) == 1)
            else:
                check(group + "_no_fabricated_plan_task", not state)
            if group == "lifecycle" and len(state) == 1:
                steps = json.loads(state[0]["step_states"])
                check("lifecycle_task_steps", [s["status"] for s in steps]
                      == ["completed", "completed"])
            if group == "revision" and len(state) == 1:
                steps = json.loads(state[0]["step_states"])
                check("revision_task_steps_pending", state[0]["plan_revision"] == 2
                      and [s["status"] for s in steps] == ["pending", "pending"])
        # Exactly one provenance-bounded execution evidence row for each
        # checkpointed M4 plan tool dispatch, keyed by SAME dispatch ID.
        rows = db.execute(
            "SELECT id,conversation_id,turn_id,step_id,tool_name,arguments_json,"
            "receipt_json,status FROM plan_tool_dispatches").fetchall()
        plan_attempts = 0
        for dispatch in rows:
            attempt = db.execute(
                "SELECT id,conversation_id,turn_id,step_id,tool_name,"
                "arguments_digest,status,result_projection "
                "FROM agent_attempts WHERE id=?", (dispatch["id"],)).fetchall()
            if dispatch["status"] == "checkpointed":
                plan_attempts += 1
                check("dispatch_" + dispatch["id"] + "_one_success",
                      len(attempt) == 1
                      and attempt[0]["conversation_id"] == dispatch["conversation_id"]
                      and attempt[0]["turn_id"] == dispatch["turn_id"]
                      and attempt[0]["step_id"] == dispatch["step_id"]
                      and attempt[0]["tool_name"] == dispatch["tool_name"]
                      and attempt[0]["status"] == "succeeded")
                if len(attempt) == 1:
                    event = db.execute(
                        "SELECT id FROM agent_state_transitions "
                        "WHERE conversation_id=? AND attempt_id=? "
                        "AND reason_code='verified_step_completed'",
                        (dispatch["conversation_id"], dispatch["id"])).fetchall()
                    check("dispatch_" + dispatch["id"] + "_causal_transition",
                          len(event) == 1)
            else:
                check("dispatch_" + dispatch["id"] + "_no_success_provenance",
                      not any(a["status"] == "succeeded" for a in attempt))
        check("exactly_two_plan_tool_evidences", plan_attempts == 2)
        # Every step transition with an attempt reference must point to a
        # successful attempt in the same conversation (no forged model IDs).
        linked = db.execute(
            "SELECT e.conversation_id,e.attempt_id,a.conversation_id AS ac,"
            "a.status AS ast FROM agent_state_transitions e "
            "LEFT JOIN agent_attempts a ON a.id=e.attempt_id "
            "WHERE e.attempt_id IS NOT NULL").fetchall()
        check("all_transition_references_durable", bool(linked) and all(
            x["conversation_id"] == x["ac"] and x["ast"] == "succeeded"
            for x in linked))
        # Normal calculate is executor-journalled, never an M4 plan dispatch.
        ordinary_cid = conversations["ordinary"]
        ordinary = db.execute(
            "SELECT id,tool_name,status,step_id FROM agent_attempts "
            "WHERE conversation_id=?", (ordinary_cid,)).fetchall()
        check("ordinary_calculate_durable", any(
            x["tool_name"] == "calculate" and x["status"] == "succeeded"
            and x["step_id"] is None for x in ordinary))
        check("ordinary_has_no_plan_dispatch", db.execute(
            "SELECT count(*) FROM plan_tool_dispatches WHERE conversation_id=?",
            (ordinary_cid,)).fetchone()[0] == 0)
        # No unfinished prepared writes / unexpected double-journal records.
        check("no_unknown_attempt_left", db.execute(
            "SELECT count(*) FROM agent_attempts WHERE status='prepared'"
        ).fetchone()[0] == 0)
        check("no_duplicate_evidence_id", db.execute(
            "SELECT count(*) FROM agent_attempts"
        ).fetchone()[0] == db.execute(
            "SELECT count(DISTINCT id) FROM agent_attempts"
        ).fetchone()[0])
        check("sqlite_integrity", db.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    finally:
        db.close()
    prior_pass = prior["summary"]["FAIL"] == 0
    all_ok = all(x["result"] == "PASS" for x in checks)
    return {
        "schema": "s25-03-step5-physical-v1",
        "baseline_m5_verdict": "PASS" if prior_pass else "FAIL",
        "verdict": "PASS" if all_ok and prior_pass else "FAIL",
        "summary": {"PASS": sum(x["result"] == "PASS" for x in checks),
                    "FAIL": sum(x["result"] == "FAIL" for x in checks)},
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--live", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    live = json.loads(args.live.read_text(encoding="utf-8-sig"))
    result = verify(args.db, live)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"],
                      "summary": result["summary"]}, ensure_ascii=False))
    raise SystemExit(0 if result["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
