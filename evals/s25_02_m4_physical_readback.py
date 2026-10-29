"""Read-only, identity-bound physical SQLite verification for M4 guarded live report.

This is independent DB evidence, not a new model execution or concurrency test.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def verify(database: Path, report: dict) -> dict:
    if report.get("schema") != "s25-m4-guarded-live-v1":
        raise ValueError("unsupported_report_schema")
    rows = report.get("records")
    if not isinstance(rows, list) or len(rows) != 5:
        raise ValueError("incomplete_report")
    if report.get("model", {}).get("provider") == "fake":
        raise ValueError("fake_provider_not_physical_evidence")
    if not database.is_file():
        raise ValueError("database_file_missing")
    checks = []
    def check(name, ok, details=None):
        checks.append({"name": name, "result": "PASS" if ok else "FAIL",
                       **(details or {})})

    con = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        ids = []
        groups = {}
        for rec in rows:
            case = rec.get("case")
            if rec.get("verdict") != "PASS" or not isinstance(rec.get("response"), dict):
                check(case + "_live_prerequisite", False)
                continue
            response = rec["response"]
            cid = rec.get("conversation_id")
            rid = rec.get("client_request_id")
            tid = response.get("turn_id")
            mid = response.get("message_id")
            check(case + "_complete_identity", all(isinstance(x, str) and x
                  for x in (cid, rid, tid, mid)))
            ids.append((cid, rid, tid))
            groups.setdefault(rec.get("group"), set()).add(cid)
            turn = con.execute(
                "SELECT state,assistant_message_id,output_chars FROM turns "
                "WHERE id=? AND conversation_id=? AND request_id=?",
                (tid, cid, rid)).fetchall()
            check(case + "_exact_turn", len(turn) == 1)
            if len(turn) == 1:
                msg = con.execute(
                    "SELECT role,status,content FROM messages WHERE id=? AND conversation_id=?",
                    (turn[0]["assistant_message_id"], cid)).fetchall()
                check(case + "_assistant_commit",
                      len(msg) == 1 and msg[0]["role"] == "assistant"
                      and msg[0]["status"] == turn[0]["state"]
                      and turn[0]["state"] == response.get("status")
                      and turn[0]["output_chars"] == len(msg[0]["content"]))
            user = con.execute(
                "SELECT count(*) FROM messages WHERE conversation_id=? AND role='user' AND content=?",
                (cid, rec.get("request"))).fetchone()[0]
            check(case + "_user_message_binding", user == 1)
        check("unique_request_identity", len(ids) == len(set(ids)) == 5)
        check("isolated_group_identity", len(groups.get("plan", set())) == 1
              and len(groups.get("cancel", set())) == 1
              and groups["plan"] != groups["cancel"])
        for group, expected in (
            ("plan", ["completed", "completed"]), ("cancel", None)):
            cids = groups.get(group, set())
            if len(cids) != 1:
                check(group + "_plan_snapshot", False)
                continue
            cid = next(iter(cids))
            db_plan = con.execute(
                "SELECT snapshot FROM conversation_plans WHERE conversation_id=?",
                (cid,)).fetchall()
            if expected is None:
                check(group + "_cancel_durable_absence", len(db_plan) == 0)
            else:
                try:
                    snapshot = json.loads(db_plan[0]["snapshot"]) if len(db_plan) == 1 else None
                    states = [s["status"] for s in snapshot["steps"]]
                    tools = [s.get("evidence_tool") for s in snapshot["steps"]]
                    check(group + "_plan_snapshot",
                          states == expected and tools == ["calculate", "calculate"]
                          and snapshot.get("revision") == 1)
                except (KeyError, TypeError, ValueError):
                    check(group + "_plan_snapshot", False)
            claims = con.execute(
                "SELECT turn_id FROM plan_execution_claims WHERE conversation_id=?",
                (cid,)).fetchall()
            # A completed lifecycle should not retain a live claim. This
            # confirms local claim cleanup; it is not proof of external exactly-once.
            check(group + "_no_active_claim", len(claims) == 0)
        check("sqlite_integrity", con.execute("PRAGMA quick_check").fetchone()[0] == "ok")
    finally:
        con.close()
    return {"schema": "s25-m4-guarded-physical-v1",
            "database_name": database.name,
            "checks": checks,
            "summary": {state: sum(x["result"] == state for x in checks)
                        for state in ("PASS", "FAIL")},
            "limitations": ["SQLite read-only state check; not multi-worker/PostgreSQL proof.",
                            "Cannot prove external side effects are exactly once.",
                            "Must use the SAME DATABASE_URL as the live API process."]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--database", required=True)
    p.add_argument("--live-report", required=True)
    p.add_argument("--output", default="m4-guarded-physical.json")
    args = p.parse_args()
    report = json.loads(Path(args.live_report).read_text(encoding="utf-8-sig"))
    result = verify(Path(args.database), report)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["summary"])
    raise SystemExit(1 if result["summary"]["FAIL"] else 0)


if __name__ == "__main__":
    main()
