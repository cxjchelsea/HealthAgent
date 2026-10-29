"""Independent read-only SQLite physical readback of M5 unified live HTTP report."""
import argparse
import json
from pathlib import Path
import sqlite3


CASES = ("CREATE_A", "FIRST", "RESUME", "REPLAY", "CREATE_B", "REVISE",
         "CREATE_C", "CANCEL", "ORDINARY", "PENDING", "PENDING_ANSWER")


def verify(db_path: Path, report: dict) -> dict:
    if (report.get("schema") != "s25-m5-unified-live-v1"
            or report.get("model", {}).get("provider") in {None, "fake"}
            or report.get("model", {}).get("m5_unified_route_enabled") is not True):
        raise ValueError("non_real_or_non_unified_report")
    records = report.get("records") or []
    if report.get("verdict") != "PASS" or tuple(x.get("case") for x in records) != CASES:
        raise ValueError("unpassed_or_incomplete_live_report")
    if not db_path.is_file():
        raise ValueError("database_missing")
    checks = []

    def check(name, ok):
        checks.append({"name": name, "result": "PASS" if ok else "FAIL"})

    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        conversations = report["conversations"]
        cases = {row["case"]: row for row in records}
        for row in records:
            case = row["case"]
            cid, rid = row["conversation_id"], row["client_request_id"]
            turn = db.execute(
                "SELECT id,state,assistant_message_id FROM turns "
                "WHERE conversation_id=? AND request_id=?", (cid, rid)
            ).fetchall()
            if case == "REPLAY":
                check("replay_single_original_turn",
                      len(turn) == 1 and turn[0]["id"] == cases["RESUME"]["response"]["turn_id"]
                      and rid == cases["RESUME"]["client_request_id"])
                continue
            response = row.get("response") or {}
            valid = (len(turn) == 1 and turn[0]["id"] == response.get("turn_id"))
            check(case + "_exact_turn", valid)
            msg = db.execute(
                "SELECT id,role,status FROM messages WHERE conversation_id=? AND id=?",
                (cid, response.get("message_id"))).fetchall()
            check(case + "_assistant_committed",
                  valid and len(msg) == 1 and msg[0]["role"] == "assistant"
                  and turn[0]["assistant_message_id"] == msg[0]["id"]
                  and msg[0]["status"] == turn[0]["state"])

        snapshots = {}
        for group, cid in conversations.items():
            records_for_group = db.execute(
                "SELECT snapshot FROM conversation_plans WHERE conversation_id=?",
                (cid,)).fetchall()
            snapshots[group] = (
                json.loads(records_for_group[0]["snapshot"])
                if len(records_for_group) == 1 else None
            )
            count = db.execute(
                "SELECT count(*) FROM plan_execution_claims WHERE conversation_id=?",
                (cid,)).fetchone()[0]
            check(group + "_no_active_claim", count == 0)

        lifecycle = snapshots["lifecycle"]
        steps = lifecycle.get("steps", []) if lifecycle else []
        check("lifecycle_both_steps_durable",
              len(steps) == 2 and [s.get("status") for s in steps]
              == ["completed", "completed"]
              and [s.get("expected_arguments", {}).get("expression") for s in steps]
              == ["3*4", "5*6"])
        revision = snapshots["revision"]
        steps = revision.get("steps", []) if revision else []
        check("revision_only_second_step_changed",
              len(steps) == 2 and all(s.get("status") == "pending" for s in steps)
              and [s.get("expected_arguments", {}).get("expression") for s in steps]
              == ["3*4", "7*8"] and revision.get("revision") == 2)
        check("cancel_removed_durable_plan", snapshots["cancel"] is None)
        check("ordinary_has_no_saved_plan", snapshots["ordinary"] is None)
        check("pending_has_no_saved_plan", snapshots["pending"] is None)

        dispatches = db.execute(
            "SELECT turn_id,step_id,tool_name,arguments_json,status "
            "FROM plan_tool_dispatches WHERE conversation_id=?",
            (conversations["lifecycle"],)).fetchall()
        by_turn = {case: cases[case]["response"].get("turn_id")
                   for case in ("FIRST", "RESUME")}
        intended = ((by_turn["FIRST"], 1, "3*4"),
                    (by_turn["RESUME"], 2, "5*6"))
        observed = []
        for row in dispatches:
            try:
                expr = json.loads(row["arguments_json"]).get("expression")
            except (ValueError, TypeError, AttributeError):
                expr = None
            observed.append((row["turn_id"], row["step_id"], row["tool_name"],
                             expr, row["status"]))
        check("exact_two_checkpointed_dispatches",
              len(observed) == 2
              and sorted(observed) == sorted(
                  (turn, step, "calculate", expression, "checkpointed")
                  for turn, step, expression in intended))
        total_dispatches = db.execute(
            "SELECT count(*) FROM plan_tool_dispatches WHERE conversation_id IN ("
            + ",".join("?" for _ in conversations) + ")",
            tuple(conversations.values())).fetchone()[0]
        check("no_other_plan_dispatch", total_dispatches == 2)
        check("sqlite_integrity", db.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    finally:
        db.close()
    return {"schema": "s25-m5-unified-physical-v1",
            "checks": checks,
            "summary": {v: sum(c["result"] == v for c in checks)
                        for v in ("PASS", "FAIL")},
            "limitations": [
                "Same isolated SQLite must be used by the API and this readback.",
                "No external exactly-once guarantee or production authorization.",
                "PostgreSQL physical E2E requires a separate backend run.",
            ]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--database", required=True)
    p.add_argument("--live-report", required=True)
    p.add_argument("--output", default="m5-unified-physical.json")
    args = p.parse_args()
    report = json.loads(Path(args.live_report).read_text(encoding="utf-8-sig"))
    result = verify(Path(args.database), report)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(result["summary"])
    raise SystemExit(1 if result["summary"]["FAIL"] else 0)


if __name__ == "__main__":
    main()
