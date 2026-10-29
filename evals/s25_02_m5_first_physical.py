"""M5 FIRST read-only SQLite physical verification for a real-model live report."""
import argparse
import json
import sqlite3
from pathlib import Path


def verify(database: Path, report: dict) -> dict:
    if report.get("schema") != "s25-m5-first-live-v1":
        raise ValueError("unsupported_report_schema")
    rows = report.get("records")
    if report.get("verdict") != "PASS" or not isinstance(rows, list):
        raise ValueError("live_report_not_passed")
    if [r.get("case") for r in rows] != ["CREATE", "FIRST", "REPLAY"]:
        raise ValueError("incomplete_live_report")
    if report.get("model", {}).get("provider") in {None, "fake"}:
        raise ValueError("real_model_required")
    if not database.is_file():
        raise ValueError("database_file_missing")
    checks = []
    def check(name, ok):
        checks.append({"name": name, "result": "PASS" if ok else "FAIL"})
    cid = report.get("conversation_id")
    con = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        for record in rows[:2]:
            case, rid = record["case"], record.get("client_request_id")
            response = record.get("response") or {}
            turn_id, mid = response.get("turn_id"), response.get("message_id")
            turn = con.execute(
                "SELECT state,assistant_message_id FROM turns WHERE "
                "conversation_id=? AND request_id=? AND id=?",
                (cid, rid, turn_id)).fetchall()
            check(case + "_exact_turn", len(turn) == 1)
            if len(turn) == 1:
                msg = con.execute(
                    "SELECT role,status FROM messages WHERE id=? AND conversation_id=?",
                    (mid, cid)).fetchall()
                check(case + "_assistant_committed",
                      len(msg) == 1 and msg[0]["role"] == "assistant"
                      and msg[0]["status"] == turn[0]["state"]
                      and turn[0]["assistant_message_id"] == mid)
        check("replay_same_request_id",
              rows[1]["client_request_id"] == rows[2]["client_request_id"])
        count = con.execute(
            "SELECT count(*) FROM turns WHERE conversation_id=? AND request_id=?",
            (cid, rows[1]["client_request_id"])).fetchone()[0]
        check("replay_no_extra_turn", count == 1)
        plan_rows = con.execute(
            "SELECT snapshot FROM conversation_plans WHERE conversation_id=?", (cid,)
        ).fetchall()
        try:
            snapshot = json.loads(plan_rows[0]["snapshot"]) if len(plan_rows) == 1 else {}
            steps = snapshot.get("steps", [])
            check("durable_first_only", len(steps) == 2
                  and [s["status"] for s in steps] == ["completed", "pending"]
                  and steps[0].get("evidence_tool") == "calculate"
                  and steps[1].get("evidence_tool") is None)
        except (ValueError, TypeError, KeyError):
            check("durable_first_only", False)
        claims = con.execute(
            "SELECT count(*) FROM plan_execution_claims WHERE conversation_id=?", (cid,)
        ).fetchone()[0]
        check("no_active_claim", claims == 0)
        dispatch = con.execute(
            "SELECT status,step_id,tool_name,turn_id,arguments_json FROM plan_tool_dispatches WHERE conversation_id=?",
            (cid,)).fetchall()
        check("exactly_one_local_checkpointed_dispatch",
              len(dispatch) == 1 and dispatch[0]["status"] == "checkpointed"
              and dispatch[0]["tool_name"] == "calculate"
              and dispatch[0]["step_id"] == 1)
        # Independently bind the durable row to the actual FIRST Turn and
        # to the exact arguments of the first saved step, not just the tool.
        # Do not trust response trace alone: use persisted step arguments.
        dispatch_bound = False
        if len(dispatch) == 1 and len(plan_rows) == 1:
            try:
                saved = json.loads(plan_rows[0]["snapshot"])
                first_step = saved["steps"][0]
                arguments = json.loads(dispatch[0]["arguments_json"])
                dispatch_bound = (
                    dispatch[0]["turn_id"] == rows[1]["response"]["turn_id"]
                    and dispatch[0]["step_id"] == first_step["id"] == 1
                    and dispatch[0]["tool_name"] == first_step["expected_tool"] == "calculate"
                    and isinstance(arguments, dict)
                    and arguments == first_step["expected_arguments"]
                    and arguments == {"expression": "3*4"}
                )
            except (KeyError, IndexError, ValueError, TypeError):
                dispatch_bound = False
        check("dispatch_first_turn_and_arguments_bound", dispatch_bound)
        check("sqlite_quick_check", con.execute("PRAGMA quick_check").fetchone()[0] == "ok")
    finally:
        con.close()
    return {"schema": "s25-m5-first-physical-v1", "checks": checks,
            "summary": {k: sum(x["result"] == k for x in checks)
                        for k in ("PASS", "FAIL")},
            "limitations": ["Local SQLite readback only; no external exactly-once guarantee.",
                            "API and verifier must share the identical isolated DB."]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--database", required=True)
    p.add_argument("--live-report", required=True)
    p.add_argument("--output", default="m5-first-physical.json")
    args = p.parse_args()
    report = json.loads(Path(args.live_report).read_text(encoding="utf-8-sig"))
    result = verify(Path(args.database), report)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(result["summary"])
    raise SystemExit(1 if result["summary"]["FAIL"] else 0)


if __name__ == "__main__":
    main()
