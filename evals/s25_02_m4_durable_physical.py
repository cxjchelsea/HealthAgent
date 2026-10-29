"""Read-only physical proof for the new M4 durable tool dispatch journal.

Run against the exact SQLite DB used for a FRESH post-migration real Qwen
m4-guarded-live report. Earlier reports without dispatch rows are incompatible.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def verify(database, report):
    if report.get("schema") != "s25-m4-guarded-live-v1":
        raise ValueError("wrong_live_schema")
    if (report.get("model", {}).get("route_admission_mode") != "guarded"
            or report.get("summary") != {"PASS": 5, "FAIL": 0, "BLOCKED": 0}):
        raise ValueError("live_report_incomplete")
    records = {r.get("case"): r for r in report.get("records", [])}
    if set(records) != {"CREATE", "FIRST", "REMAINING", "CANCEL_PREPARE", "CANCEL"}:
        raise ValueError("wrong_cases")
    checks = []
    def check(name, ok):
        checks.append({"name": name, "result": "PASS" if ok else "FAIL"})
    path = Path(database)
    if not path.is_file():
        raise ValueError("database_file_missing")
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        check("dispatch_migration", version == "0010_plan_recovery_audit")
        expected = {"FIRST": (1, "3*4", "12"), "REMAINING": (2, "5*6", "30")}
        for case, (step, expression, value) in expected.items():
            rec = records[case]
            rows = con.execute(
                "SELECT d.step_id,d.tool_name,d.arguments_json,d.receipt_json,d.status "
                "FROM plan_tool_dispatches d JOIN turns t ON t.id=d.turn_id "
                "WHERE d.turn_id=? AND d.conversation_id=? AND t.request_id=?",
                (rec["response"]["turn_id"], rec["conversation_id"],
                 rec["client_request_id"])).fetchall()
            check(case + "_one_dispatch", len(rows) == 1)
            if len(rows) == 1:
                row = rows[0]
                try:
                    args = json.loads(row["arguments_json"])
                    receipt = json.loads(row["receipt_json"])
                    valid = (row["step_id"] == step
                             and row["tool_name"] == "calculate"
                             and args == {"expression": expression}
                             and receipt.get("ok") is True
                             and receipt.get("value") == value
                             and row["status"] == "checkpointed")
                except (ValueError, TypeError):
                    valid = False
                check(case + "_exact_tool_receipt_commit", valid)
        for case in ("CREATE", "CANCEL_PREPARE", "CANCEL"):
            rec = records[case]
            count = con.execute(
                "SELECT count(*) FROM plan_tool_dispatches WHERE turn_id=?",
                (rec["response"]["turn_id"],)).fetchone()[0]
            check(case + "_no_dispatch", count == 0)
        check("sqlite_quick_check",
              con.execute("PRAGMA quick_check").fetchone()[0] == "ok")
    finally:
        con.close()
    return {"schema": "s25-m4-durable-physical-v1",
            "database_name": path.name, "checks": checks,
            "summary": {key: sum(x["result"] == key for x in checks)
                        for key in ("PASS", "FAIL")},
            "limitations": ["This is a read-only SQLite observation, not external exactly-once proof.",
                            "Must confirm identical SQLite database URL for API and verifier.",
                            "Requires a NEW guarded live run after migration 0010."]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--database", required=True)
    p.add_argument("--live-report", required=True)
    p.add_argument("--output", default="m4-durable-physical.json")
    args = p.parse_args()
    report = json.loads(Path(args.live_report).read_text(encoding="utf-8-sig"))
    result = verify(args.database, report)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(result["summary"])
    raise SystemExit(1 if result["summary"]["FAIL"] else 0)


if __name__ == "__main__":
    main()
