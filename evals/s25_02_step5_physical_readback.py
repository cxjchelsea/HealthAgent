"""Read-only physical DB acceptance of S25-02 third-round isolated SQLite run.

Does not issue tool calls or change database rows. Match unique conversations
using exact test prompts and reject ambiguous or incomplete evidence.
"""
import argparse
import json
import sqlite3
from pathlib import Path


CASES = {
    "plan": (
        "请建立一个暂不执行的两步正式计划：先查询 RAG，再计算 8*8。",
        "请将刚才正式计划的第二步计算 8*8 修改成 9*9，其他不变，不执行。",
        "现在只执行已保存计划的第一步，其余步骤先不执行。",
        "继续执行已保存计划剩余的所有步骤。",
    ),
    "probe": (
        "请建立一个暂不执行的两步正式计划：先查询 RAG，再计算 8*8。",
        "请单独使用计算工具计算 19+23，不要执行刚才的计划。",
    ),
    "task": ("请正式创建任务：周五提交测试报告。",),
    "draft": ("拟定三项工作任务草案，不要保存也不要创建计划。",),
    "clarify": (
        "请在 MCP 资料中查一个词条，但还没说词条。",
        "RAG",
    ),
}
TOTAL_CASES = 14


def check_db(path: Path, report: dict) -> dict:
    if not path.is_file():
        raise ValueError("database_file_missing")
    rows = report.get("rows", [])
    if len(rows) != TOTAL_CASES or report.get("model", {}).get("provider") != "ollama_proxy":
        raise ValueError("incompatible_live_report")
    if (report.get("summary", {}).get("FAIL") != 0
            or report.get("summary", {}).get("BLOCKED") != 0):
        raise ValueError("report_has_blocking_cases")
    # SQLite URI mode=ro prevents DB writes. Avoid leaking raw user data.
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    checks = []
    found = {}
    try:
        exact_conversations = report.get("conversations", {})
        for label, prompts in CASES.items():
            report_group = "plan_probe" if label == "probe" else label
            supplied = exact_conversations.get(report_group)
            candidates = ([{"conversation_id": supplied}] if supplied else
                con.execute(
                    "SELECT DISTINCT conversation_id FROM messages "
                    "WHERE role='user' AND content=?", (prompts[0],)).fetchall())
            matches = []
            for candidate in candidates:
                cid = candidate["conversation_id"]
                user_messages = [x[0] for x in con.execute(
                    "SELECT content FROM messages WHERE conversation_id=? "
                    "AND role='user' ORDER BY created_at, id", (cid,))]
                if user_messages == list(prompts):
                    matches.append(cid)
            if len(matches) != 1:
                checks.append({"name": label + "_unique_conversation",
                               "result": "FAIL", "matched": len(matches),
                               "identity_source": ("report" if supplied else "prompt_scan")})
            else:
                found[label] = matches[0]
                checks.append({"name": label + "_unique_conversation",
                               "result": "PASS",
                               "identity_source": ("report" if supplied else "prompt_scan")})
        if len(set(found.values())) != len(found):
            checks.append({"name": "conversation_isolation", "result": "FAIL"})
        else:
            checks.append({"name": "conversation_isolation",
                           "result": "PASS" if len(found) == len(CASES) else "BLOCKED"})
        identity_rows = [row for row in rows if row.get("conversation_id")
                         and row.get("client_request_id") and row.get("turn_id")]
        if identity_rows:
            bound = 0
            for row in identity_rows:
                match = con.execute(
                    "SELECT count(*) FROM turns WHERE id=? AND conversation_id=? "
                    "AND request_id=?",
                    (row["turn_id"], row["conversation_id"],
                     row["client_request_id"])).fetchone()[0]
                bound += match == 1
            checks.append({"name":"exact_turn_request_binding",
                           "result":"PASS" if bound == len(rows) else "FAIL",
                           "bound":bound,"expected":len(rows)})
        elif report.get("route_shadow_required"):
            checks.append({"name":"exact_turn_request_binding",
                           "result":"BLOCKED","bound":0,"expected":len(rows)})
        for label, cid in found.items():
            turns = con.execute(
                "SELECT t.state,t.failure_code,t.output_chars,m.status,m.content "
                "FROM turns t JOIN messages m ON m.id=t.assistant_message_id "
                "WHERE t.conversation_id=?", (cid,)).fetchall()
            good = len(turns) == len(CASES[label]) and all(
                t["state"] in ("completed", "partial")
                and t["status"] == t["state"]
                and t["output_chars"] == len(t["content"]) for t in turns)
            checks.append({"name": label + "_turn_message_consistency",
                           "result": "PASS" if good else "FAIL",
                           "turns": len(turns)})
        for label in ("plan", "probe"):
            if label not in found:
                continue
            records = con.execute(
                "SELECT snapshot FROM conversation_plans WHERE conversation_id=?",
                (found[label],)).fetchall()
            try:
                snap = json.loads(records[0][0]) if len(records) == 1 else None
                steps = snap["steps"] if isinstance(snap, dict) else []
                expected_states = (["completed", "completed"] if label == "plan"
                                   else ["pending", "pending"])
                expected_args = (["RAG", "9*9"] if label == "plan"
                                 else ["RAG", "8*8"])
                good = (len(steps) == 2
                        and [s.get("status") for s in steps] == expected_states
                        and [s.get("expected_tool") for s in steps] ==
                        ["mcp_reference_lookup", "calculate"]
                        and [s.get("expected_arguments", {}).get(
                            "term" if i == 0 else "expression")
                             for i, s in enumerate(steps)] == expected_args
                        and (label != "plan" or all(
                            s.get("evidence_tool") == s.get("expected_tool")
                            for s in steps)))
                checks.append({"name": label + "_physical_plan_snapshot",
                               "result": "PASS" if good else "FAIL",
                               "states": [s.get("status") for s in steps],
                               "revision": snap.get("revision") if snap else None})
            except (ValueError, TypeError, KeyError):
                checks.append({"name": label + "_physical_plan_snapshot",
                               "result": "FAIL"})
        if "task" in found:
            task = con.execute(
                "SELECT title FROM conversation_tasks WHERE conversation_id=?",
                (found["task"],)).fetchall()
            checks.append({"name": "task_exactly_one",
                           "result": "PASS" if len(task) == 1
                           and task[0][0] == "周五提交测试报告" else "FAIL",
                           "count": len(task)})
        for label in ("draft", "plan", "probe", "clarify"):
            if label in found:
                n = con.execute(
                    "SELECT count(*) FROM conversation_tasks WHERE conversation_id=?",
                    (found[label],)).fetchone()[0]
                checks.append({"name": label + "_no_unexpected_task",
                               "result": "PASS" if n == 0 else "FAIL",
                               "count": n})
        # No file modification, but check visible integrity through read-only connection.
        integrity = con.execute("PRAGMA quick_check").fetchone()[0]
        checks.append({"name": "sqlite_quick_check",
                       "result": "PASS" if integrity == "ok" else "FAIL"})
    finally:
        con.close()
    return {"source": "readonly_sqlite_physical_readback",
            "database_name": path.name,
            "summary": {status: sum(x["result"] == status for x in checks)
                        for status in ("PASS", "FAIL", "BLOCKED")},
            "checks": checks,
            "limitations": [
                "Conversation matching uses exact test messages; ambiguity fails closed.",
                "Local storage consistency does not prove crash-safe external side effects.",
                "This is a physical readback, not a new live-model execution."
            ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--live-report", required=True)
    parser.add_argument("--output", default="step5-physical-readback.json")
    args = parser.parse_args()
    report = json.loads(Path(args.live_report).read_text(encoding="utf-8"))
    result = check_db(Path(args.database), report)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    print("Saved:", args.output)
    raise SystemExit(0 if result["summary"]["FAIL"] == 0
                     and result["summary"]["BLOCKED"] == 0 else 1)


if __name__ == "__main__":
    main()
