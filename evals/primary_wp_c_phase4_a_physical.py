"""WP-C Phase 4-A: independent read-only SQLite evidence audit after real Qwen turns.

Use the SAME disposable SQLite file as the separately running local API. Never
connect to production or user data. Store only counts, route metadata and
random IDs in reports; no messages, task titles or tool arguments.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import httpx

SCENARIOS = (
    ("task", (
        ("origin", "我想登记一个待办，但是还没决定名称。先问我具体名字。"),
        ("answer", "盘点模拟传感器标签"),
        ("replay", "盘点模拟传感器标签"),
    )),
    ("cancel", (
        ("origin", "我要创建一条待办，但名称待定，请先问我叫什么。"),
        ("withdraw", "算了，这个待办不创建了。"),
    )),
    ("mcp", (
        ("origin", "我想查技术术语解释，还没选好术语，请先询问。"),
        ("answer", "RAG"),
        ("replay", "RAG"),
    )),
)


def head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def open_readonly(path: Path):
    """SQLite URI mode=ro guarantees this verifier cannot mutate the API DB."""
    if not path.is_file():
        raise ValueError("physical_database_missing")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def rows(db, table, conversation_id):
    # Table names are fixed here, not client-supplied values.
    allowed = {"turns", "primary_route_evidence", "pending_write_authorities",
               "conversation_tasks", "agent_attempts"}
    if table not in allowed:
        raise ValueError("unknown_audit_table")
    db.row_factory = sqlite3.Row
    return [dict(row) for row in db.execute(
        "SELECT * FROM " + table + " WHERE conversation_id = ?",
        (conversation_id,))]


def audit(db, conversation_id: str, group: str, turn_ids: dict) -> dict:
    """Read independent persisted state, not the HTTP trace.

    Return bounded, non-sensitive evidence. Every assertion is false until
    positively supported by a matching DB row.
    """
    turns = rows(db, "turns", conversation_id)
    records = rows(db, "primary_route_evidence", conversation_id)
    auth = rows(db, "pending_write_authorities", conversation_id)
    tasks = rows(db, "conversation_tasks", conversation_id)
    attempts = rows(db, "agent_attempts", conversation_id)
    turn_by_id = {t["id"]: t for t in turns}
    ev_by_turn = {e["turn_id"]: e for e in records}
    ids = [turn_ids[k] for k in turn_ids if k != "replay"]
    evidence_ok = (
        len(turns) == len(ids)
        and len(records) == len(ids)
        and len(set(ids)) == len(ids)
        and all(tid in turn_by_id and tid in ev_by_turn
                and turn_by_id[tid]["conversation_id"] == conversation_id
                and ev_by_turn[tid]["schema_version"] == "primary-route-v1"
                and ev_by_turn[tid]["semantic_attempts"] == 1
                and len(ev_by_turn[tid]["preturn_snapshot_digest"]) == 64
                and ev_by_turn[tid]["proposal_id"]
                and ev_by_turn[tid]["admission_reason"] for tid in ids)
    )
    origin = turn_ids.get("origin")
    second = turn_ids.get("answer", turn_ids.get("withdraw"))
    origin_ev = ev_by_turn.get(origin, {})
    second_ev = ev_by_turn.get(second, {})
    create_receipts = [a for a in attempts
                       if a["tool_name"] == "create_task"
                       and a["status"] == "succeeded"]
    mcp_receipts = [a for a in attempts
                    if a["tool_name"] == "mcp_reference_lookup"
                    and a["status"] == "succeeded"]
    origin_ok = (origin_ev.get("route_kind") == "clarify"
                 and origin_ev.get("disposition") == "clarify"
                 and turn_by_id.get(origin, {}).get("state") == "partial")
    if group == "task":
        effect_ok = (
            len(auth) == len(tasks) == len(create_receipts) == 1
            and len(attempts) == 1 and len(mcp_receipts) == 0
            and auth[0]["status"] == "consumed"
            and auth[0]["origin_turn_id"] == origin
            and auth[0]["consumed_turn_id"] == second
            and auth[0]["tool_name"] == "create_task"
            and create_receipts[0]["turn_id"] == second
            and second_ev.get("route_kind") == "clarify_response"
            and second_ev.get("disposition") == "allow"
            and second_ev.get("admission_reason") ==
                "primary_v1_pending_task_authorized"
            and turn_by_id.get(second, {}).get("state") == "completed")
    elif group == "cancel":
        effect_ok = (
            len(auth) == 1 and len(tasks) == len(attempts) == 0
            and auth[0]["status"] == "revoked"
            and auth[0]["consumed_turn_id"] is None
            and auth[0]["origin_turn_id"] == origin
            and second_ev.get("route_kind") in {"respond", "clarify", "reject",
                                                  "clarify_response"}
            and second_ev.get("disposition") in {"allow", "clarify", "restrict"}
            and turn_by_id.get(second, {}).get("state") in {"completed", "partial"})
    else:
        effect_ok = (
            len(auth) == len(tasks) == len(create_receipts) == 0
            and len(mcp_receipts) == len(attempts) == 1
            and mcp_receipts[0]["turn_id"] == second
            and second_ev.get("route_kind") == "clarify_response"
            and second_ev.get("disposition") == "allow"
            and second_ev.get("admission_reason") == "primary_v1_pending_read_only"
            and turn_by_id.get(second, {}).get("state") == "completed")
    return {
        "group": group, "conversation_id": conversation_id,
        "turn_count": len(turns), "primary_evidence_count": len(records),
        "authority_statuses": [a["status"] for a in auth],
        "task_count": len(tasks), "attempt_count": len(attempts),
        "successful_task_receipts": len(create_receipts),
        "successful_mcp_receipts": len(mcp_receipts),
        "evidence_ok": bool(evidence_ok and origin_ok),
        "effect_ok": bool(effect_ok),
        "verdict": "PASS" if evidence_ok and origin_ok and effect_ok else "FAIL",
    }


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8012")
    parser.add_argument("--sqlite", required=True,
                        help="Exact local disposable SQLite file also used by the API")
    parser.add_argument("--output", default="artifacts/wp-c/phase4-a-physical.json")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    parsed = urlparse(args.endpoint)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or not parsed.port or parsed.username or parsed.password
            or parsed.path not in {"", "/"}):
        parser.error("only explicit local HTTP API allowed")
    db_path = Path(args.sqlite)
    if db_path.suffix != ".sqlite" or not db_path.is_file():
        parser.error("SQLite database must exist, be disposable, and have .sqlite extension")
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "wp-c-phase4-a-physical-v1",
        "head": head(), "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "synthetic_disposable_sqlite_readonly_audit",
        "cases": [], "verdict": "FAIL",
    }

    def persist():
        report["verdict"] = ("PASS" if len(report["cases"]) == 4
                             and all(c["verdict"] == "PASS"
                                     for c in report["cases"]) else "FAIL")
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REPORT", destination.resolve(), report["verdict"], flush=True)

    try:
        with open_readonly(db_path) as db:
            with httpx.Client(timeout=args.timeout, trust_env=False) as client:
                url = args.endpoint.rstrip("/")
                model = client.get(url + "/model").json()
                good = (
                    client.get(url + "/readyz").status_code == 200
                    and model.get("provider") == "ollama_proxy"
                    and model.get("model") == "qwen3:14b"
                    and model.get("routing_profile") == "unified"
                    and model.get("primary_route_v1_active") is True
                    and model.get("route_admission_mode") == "guarded"
                    and model.get("understanding_strategy") == "structured")
                report["cases"].append({"case": "preflight",
                                        "verdict": "PASS" if good else "FAIL"})
                if not good:
                    persist()
                    return 2
                for group, actions in SCENARIOS:
                    cid = client.post(url + "/conversations").json()["id"]
                    if any(rows(db, table, cid) for table in (
                            "turns", "primary_route_evidence", "pending_write_authorities",
                            "conversation_tasks", "agent_attempts")):
                        raise ValueError("nonempty_audit_conversation")
                    ids = {}
                    replies = []
                    rid = None
                    for step, content in actions:
                        if step != "replay":
                            rid = "wp-c-p4-" + uuid.uuid4().hex[:14]
                        response = client.post(
                            url + f"/conversations/{cid}/agent/messages",
                            json={"content": content, "client_request_id": rid})
                        data = response.json()
                        ids[step] = data.get("turn_id")
                        replies.append({"step": step, "status": response.status_code,
                                        "state": data.get("status"),
                                        "reason": data.get("termination_reason")})
                    check = audit(db, cid, group, ids)
                    replay = next((x for x in replies if x["step"] == "replay"), None)
                    replay_ok = (replay is None or replay["status"] == 409)
                    origin_ok = replies[0]["status"] == 200 and replies[0]["reason"] == "needs_input"
                    effect_reason = (
                        "pending_task_created" if group == "task"
                        else "verified_clarification_execution" if group == "mcp"
                        else None)
                    reply_ok = (replies[1]["status"] == 200
                                and replies[1]["reason"] == effect_reason
                                if group != "cancel" else
                                replies[1]["status"] == 200
                                and replies[1]["reason"] != "pending_task_created")
                    check["http_ok"] = bool(replay_ok and origin_ok and reply_ok)
                    check["verdict"] = ("PASS" if check["verdict"] == "PASS"
                                        and check["http_ok"] else "FAIL")
                    report["cases"].append(check)
                    print(group, check["verdict"], flush=True)
        persist()
        return 0 if report["verdict"] == "PASS" else 1
    except (OSError, ValueError, KeyError, sqlite3.Error, httpx.HTTPError) as exc:
        report["error_type"] = type(exc).__name__
        persist()
        return 2


if __name__ == "__main__":
    sys.exit(run())
