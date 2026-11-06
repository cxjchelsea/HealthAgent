"""AC1–AC5 full-business real Qwen acceptance v2, with explicit Pending confirmation.

Use only a LOCAL API attached to a fresh disposable SQLITE database.
The runner verifies real HTTP semantics and physical SQLite rows through a
separate read-only connection. It never stores messages or model answers in
the shareable report. Once first executed, this is a known regression suite,
NOT an unseen challenge set anymore.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import httpx


# Frozen v2 before first model run. Never rewrite observed cases in place.
# Case keys are also the order of execution within their isolated conversation.
CASES = (
    ("PLAN_CREATE_A", "plan_a",
     "制定一份计算清单：第一项求 11+14，第二项求 16+23。现在只归档计划，别开始计算。", False),
    ("PLAN_FIRST_A", "plan_a",
     "刚才那份计算清单仅做第一项，第二项保持未执行。", False),
    ("PLAN_RESUME_A", "plan_a",
     "把这份清单中还没处理的项目继续完成。", False),
    ("PLAN_CREATE_B", "plan_b",
     "拟一个两步计算安排：先求 7+15，再求 16+23，先保存不执行。", False),
    ("PLAN_REVISE_B", "plan_b",
     "把保存安排的第二步从 16+23 调整为 20+21，第一步不要动。", False),
    ("PLAN_CANCEL_B", "plan_b",
     "这份保存的两步计算安排作废，删除计划，不执行。", False),
    ("ORDINARY_CALCULATE", "ordinary_calculation",
     "请使用计算工具求 (31+11)*2 的结果。", False),
    ("TASK_WRITE", "task",
     "给当前会话添加一项待办，名称是“检查合成环境的测试标签”。", False),
    ("TASK_REPLAY", "task",
     "给当前会话添加一项待办，名称是“检查合成环境的测试标签”。", False),
    ("MEMORY_DENY", "mem_no",
     "请把虚构标记 RUNT-92 长期记下来，key 为 synthetic_marker，value 为 RUNT-92。", False),
    ("MEMORY_ALLOW", "mem_yes",
     "请把虚构标记 RUNT-93 长期记下来，key 为 synthetic_marker，value 为 RUNT-93。", True),
    ("PENDING_TASK_ORIGIN", "pending_task",
     "想留一条日后要办的事项，但还没决定名称；你先问清标题再动手。", False),
    ("PENDING_TASK_ANSWER", "pending_task",
     "核对虚构样机的校准编号", False),
    ("PENDING_TASK_CONFIRM", "pending_task", "[structured_confirm_only]", False),
    ("PENDING_TASK_REPLAY", "pending_task",
     "核对虚构样机的校准编号", False),
    ("PENDING_CANCEL_ORIGIN", "pending_cancel",
     "先帮我准备新增一项待办，但标题尚未决定，请先问我。", False),
    ("PENDING_CANCEL_REPLY", "pending_cancel",
     "取消刚才那条待办的创建请求，别替我新增任务。", False),
    ("PENDING_TOPIC_ORIGIN", "pending_topic",
     "我想新增待办但还没决定名称，请先问我标题。", False),
    ("PENDING_TOPIC_REPLY", "pending_topic",
     "请介绍一下服务网格能解决什么问题", False),
    ("PENDING_MCP_ORIGIN", "pending_mcp",
     "我想从技术术语表查释义，但是还没提供具体术语，请先追问。", False),
    ("PENDING_MCP_ANSWER", "pending_mcp", "MCP", False),
    ("UNKNOWN_PLAN", "no_plan",
     "直接继续执行这段对话中先前保存的计划，不要创建新的。", False),
)

LEGACY_CATEGORIES = (
    "plan_intent", "plan_revision", "plan_cancellation",
    "plan_execution_authorization", "plan_execution_scope",
    "pending_reply", "plan_route", "plan_create",
)
ROUTES = {
    "PLAN_CREATE_A": ("plan_create", "allow", "plan_created_pending_execution"),
    "PLAN_FIRST_A": ("plan_execute_first", "allow", "pending_plan_steps"),
    "PLAN_RESUME_A": ("plan_resume", "allow", "plan_execution_completed"),
    "PLAN_CREATE_B": ("plan_create", "allow", "plan_created_pending_execution"),
    "PLAN_REVISE_B": ("plan_revise", "allow", "plan_revised_pending_execution"),
    "PLAN_CANCEL_B": ("plan_cancel", "allow", "plan_cancelled"),
    "ORDINARY_CALCULATE": ("ordinary_tool", "allow", "model_final"),
    "TASK_WRITE": ("ordinary_tool", "allow", "primary_v1_task_created"),
    "MEMORY_DENY": ("ordinary_tool", "deny", "primary_v1_memory_consent_required"),
    "MEMORY_ALLOW": ("ordinary_tool", "allow", "primary_v1_memory_saved"),
    "PENDING_TASK_ORIGIN": ("clarify", "clarify", "needs_input"),
    "PENDING_TASK_ANSWER": ("clarify_response", "allow", "pending_write_confirmation_required"),
    "PENDING_CANCEL_ORIGIN": ("clarify", "clarify", "needs_input"),
    "PENDING_TOPIC_ORIGIN": ("clarify", "clarify", "needs_input"),
    "PENDING_TOPIC_REPLY": ("respond", "allow", "model_final"),
    "PENDING_MCP_ORIGIN": ("clarify", "clarify", "needs_input"),
    "PENDING_MCP_ANSWER": ("clarify_response", "allow", "verified_clarification_execution"),
    "UNKNOWN_PLAN": ("plan_missing", "restrict", "primary_v1_missing_plan_status"),
}


def git_head():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True,
        stderr=subprocess.DEVNULL).strip()


def frozen_sha():
    return hashlib.sha256(json.dumps(
        CASES, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def read_rows(db, table, cid):
    if table not in {"turns", "primary_route_evidence",
                     "pending_write_authorities", "conversation_tasks",
                     "agent_attempts", "conversation_plans"}:
        raise ValueError("unsupported_table")
    return [dict(r) for r in db.execute(
        "SELECT * FROM " + table + " WHERE conversation_id = ?", (cid,))]


def physical_audit(db, cid, name, turn_id):
    turns = read_rows(db, "turns", cid)
    evidence = read_rows(db, "primary_route_evidence", cid)
    auth = read_rows(db, "pending_write_authorities", cid)
    tasks = read_rows(db, "conversation_tasks", cid)
    attempts = read_rows(db, "agent_attempts", cid)
    plan_records = read_rows(db, "conversation_plans", cid)
    memories = db.execute(
        "SELECT key, value FROM user_memories WHERE key = ?",
        ("synthetic_marker",)).fetchall()
    by_turn = [e for e in evidence if e["turn_id"] == turn_id]
    by_auth_status = sorted(a["status"] for a in auth)
    result = {
        "turn_count": len(turns),
        "primary_count": len(evidence),
        "task_count": len(tasks),
        "attempt_count": len(attempts),
        "authority_statuses": by_auth_status,
        "plan_rows": len(plan_records),
        "memory_rows": len(memories),
        "turn_evidence_ok": (len(by_turn) == 1
                            and by_turn[0]["schema_version"] == "primary-route-v1"
                            and by_turn[0]["semantic_attempts"] == 1
                            and len(by_turn[0]["preturn_snapshot_digest"]) == 64),
    }
    # Per-conversation cumulative effects. Exact expected numbers are fixed
    # by the frozen CASES; an HTTP trace cannot substitute for these checks.
    expected = {
        # Plan FIRST/RESUME execute read-only calculate tools and legitimately
        # persist one durable AgentAttempt per completed step.
        "PLAN_FIRST_A": (0, 1, []),
        "PLAN_RESUME_A": (0, 2, []),
        "ORDINARY_CALCULATE": (0, 1, []),
        "TASK_WRITE": (1, 1, []),
        "MEMORY_DENY": (0, 0, []),
        "MEMORY_ALLOW": (0, 1, []),
        "PENDING_TASK_ORIGIN": (0, 0, ["pending"]),
        "PENDING_TASK_ANSWER": (0, 0, ["awaiting_confirmation"]),
        "PENDING_TASK_CONFIRM": (1, 1, ["consumed"]),
        "PENDING_CANCEL_ORIGIN": (0, 0, ["pending"]),
        "PENDING_TOPIC_ORIGIN": (0, 0, ["pending"]),
        "PENDING_TOPIC_REPLY": (0, 0, ["revoked"]),
        "PENDING_CANCEL_REPLY": (0, 0, ["revoked"]),
        "PENDING_MCP_ORIGIN": (0, 0, []),
        "PENDING_MCP_ANSWER": (0, 1, []),
        "UNKNOWN_PLAN": (0, 0, []),
    }
    if name in expected:
        expected_tasks, expected_attempts, expected_auth = expected[name]
        result["effect_ok"] = (
            len(tasks) == expected_tasks
            and len(attempts) == expected_attempts
            and by_auth_status == expected_auth)
    else:
        result["effect_ok"] = len(tasks) == 0 and len(attempts) == 0

    if name in {"PLAN_CREATE_A", "PLAN_FIRST_A", "PLAN_RESUME_A",
                "PLAN_CREATE_B", "PLAN_REVISE_B"}:
        result["effect_ok"] &= len(plan_records) == 1
        if len(plan_records) == 1:
            snapshot = json.loads(plan_records[0]["snapshot"])
            statuses = [x.get("status") for x in snapshot.get("steps", [])]
            required = {
                "PLAN_CREATE_A": ["pending", "pending"],
                "PLAN_FIRST_A": ["completed", "pending"],
                "PLAN_RESUME_A": ["completed", "completed"],
                "PLAN_CREATE_B": ["pending", "pending"],
                "PLAN_REVISE_B": ["pending", "pending"],
            }
            result["effect_ok"] &= statuses == required[name]
            if name == "PLAN_REVISE_B":
                result["effect_ok"] &= (snapshot["steps"][1].get(
                    "expected_arguments") == {"expression": "20+21"})
    if name == "PLAN_CANCEL_B":
        result["effect_ok"] &= not plan_records or not json.loads(
            plan_records[0]["snapshot"]).get("steps")
    if name == "MEMORY_DENY":
        result["effect_ok"] &= len(memories) == 0
    if name == "MEMORY_ALLOW":
        result["effect_ok"] &= (len(memories) == 1
                                 and memories[0]["value"] == "RUNT-93")
    if name in {"PLAN_FIRST_A", "PLAN_RESUME_A", "ORDINARY_CALCULATE"}:
        result["effect_ok"] &= all(
            a["tool_name"] == "calculate" and a["status"] == "succeeded"
            for a in attempts)
    if name == "PENDING_TASK_ANSWER":
        result["effect_ok"] &= (
            len(auth) == 1 and auth[0]["candidate_turn_id"] == turn_id
            and auth[0]["consumed_turn_id"] is None
            and bool(auth[0]["candidate_digest"])
            and bool(auth[0]["confirmation_nonce"])
            and bool(auth[0]["candidate_expires_at"]))
    if name == "PENDING_TASK_CONFIRM":
        confirmed_turn = [t for t in turns if t["id"] == turn_id]
        result["effect_ok"] &= (
            bool(turn_id) and len(confirmed_turn) == 1
            and confirmed_turn[0]["state"] == "completed"
            and len(evidence) == 2 and len(turns) == 3
            and len(auth) == 1 and auth[0]["consumed_turn_id"] == turn_id
            and len(attempts) == 1
            and attempts[0]["turn_id"] == turn_id
            and attempts[0]["tool_name"] == "create_task"
            and attempts[0]["status"] == "succeeded")
    if name in {"PENDING_CANCEL_REPLY", "PENDING_TOPIC_REPLY"}:
        result["effect_ok"] &= auth[0]["consumed_turn_id"] is None
    if name == "TASK_WRITE":
        result["effect_ok"] &= (
            attempts[0]["tool_name"] == "create_task"
            and attempts[0]["status"] == "succeeded")
    if name == "MEMORY_ALLOW":
        result["effect_ok"] &= (
            attempts[0]["tool_name"] == "save_memory"
            and attempts[0]["status"] == "succeeded")
    if name == "PENDING_MCP_ANSWER":
        result["effect_ok"] &= (
            attempts[0]["tool_name"] == "mcp_reference_lookup"
            and attempts[0]["status"] == "succeeded")
    return result


def assess(name, http_status, body, evidence, categories, attempts, events,
           plan, prior, physical):
    if name.endswith("_REPLAY"):
        return (http_status == 409
                and body.get("detail") == "duplicate_request_id"
                and attempts == "0" and not categories and not events
                and physical["unchanged"])
    if name == "PENDING_TASK_CONFIRM":
        return (http_status == 200
                and body.get("status") == "completed"
                and body.get("termination_reason") == "pending_confirmed_task_created"
                and attempts == "0" and not categories and not events
                and physical["effect_ok"])
    if (http_status != 200 or not isinstance(evidence, dict)
            or evidence.get("schema_version") != "primary-route-v1"
            or evidence.get("semantic_attempts") != 1
            or categories.get("primary") != 1
            or any(categories.get(k, 0) for k in LEGACY_CATEGORIES)
            or not physical["turn_evidence_ok"]
            or not physical["effect_ok"]):
        return False
    route = evidence.get("route_kind")
    disposition = evidence.get("disposition")
    reason = body.get("termination_reason")
    if name == "PENDING_CANCEL_REPLY":
        # Safety-only cancellation is insufficient: semantics must be correct.
        return (route == "respond" and disposition == "allow"
                and reason == "model_final" and not events)
    if (route, disposition, reason) != ROUTES.get(name):
        return False
    if name in {"PLAN_CREATE_A", "PLAN_CREATE_B"}:
        return len(plan) == 2 and [s.get("status") for s in plan] == [
            "pending", "pending"] and not events
    if name == "PLAN_FIRST_A":
        return ([s.get("status") for s in plan] == ["completed", "pending"]
                and events == [{"name": "calculate", "ok": True}])
    if name == "PLAN_RESUME_A":
        return ([s.get("status") for s in plan] == ["completed", "completed"]
                and events == [{"name": "calculate", "ok": True}])
    if name == "ORDINARY_CALCULATE":
        return (events == [{"name": "calculate", "ok": True}]
                and "84" in str(body.get("answer", "")))
    if name == "PENDING_TOPIC_REPLY":
        return body.get("status") == "completed" and not events
    if name == "PLAN_REVISE_B":
        return (len(plan) == 2
                and [s.get("status") for s in plan] == ["pending", "pending"]
                and plan[1].get("expected_arguments") == {"expression": "20+21"}
                and not events)
    if name == "PLAN_CANCEL_B":
        return not plan and not events
    if name == "TASK_WRITE":
        return events == [{"name": "create_task", "ok": True}]
    if name == "PENDING_TASK_ANSWER":
        token = body.get("confirmation")
        return (body.get("status") == "partial" and not events
                and isinstance(token, dict)
                and all(token.get(k) for k in ("authority_id",
                    "confirmation_nonce", "candidate_digest", "candidate_title")))
    if name == "MEMORY_ALLOW":
        return events == [{"name": "save_memory", "ok": True}]
    if name == "PENDING_MCP_ANSWER":
        return events == [{"name": "mcp_reference_lookup", "ok": True}]
    if name in {"PENDING_TASK_ORIGIN", "PENDING_CANCEL_ORIGIN",
                "PENDING_TOPIC_ORIGIN", "PENDING_MCP_ORIGIN",
                "MEMORY_DENY", "UNKNOWN_PLAN"}:
        return not events
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8012")
    parser.add_argument("--sqlite", type=Path, required=True,
                        help="Exact isolated SQLite file used by the API")
    parser.add_argument("--output", default="artifacts/wp-f/ac1-ac5-business-v2.json")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    parsed = urlparse(args.endpoint)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or not parsed.port or parsed.username or parsed.password
            or parsed.path not in ("", "/")):
        parser.error("only local HTTP endpoints permitted")
    path = args.sqlite.resolve()
    if not path.is_file():
        parser.error("isolated SQLite database missing; start API first")
    destination = Path(args.output)
    if destination.exists():
        parser.error("refuse to overwrite existing report")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as check:
        check.execute("SELECT 1 FROM primary_route_evidence LIMIT 1")
        if check.execute("SELECT COUNT(*) FROM turns").fetchone()[0]:
            parser.error("fresh empty SQLite required")
    report = {
        "schema_version": "ac1-ac5-business-physical-v2",
        "head": git_head(),
        "manifest_sha256": frozen_sha(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "full_business_qwen_disposable_sqlite_pending_confirm_v2",
        "cases": [], "verdict": "FAIL",
    }
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def add(name, verdict, **info):
        report["cases"].append({"case": name, "verdict": verdict, **info})
        print(name, verdict, flush=True)

    def save():
        report["summary"] = {v: sum(c["verdict"] == v for c in report["cases"])
                             for v in ("PASS", "FAIL", "BLOCKED")}
        report["verdict"] = ("PASS" if
            len(report["cases"]) == 1 + len(CASES)
            and report["summary"]["PASS"] == len(report["cases"])
            else "FAIL")
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print("REPORT", destination.resolve(), report["verdict"], flush=True)

    try:
        with httpx.Client(timeout=args.timeout, trust_env=False) as client:
            url = args.endpoint.rstrip("/")
            m = client.get(url + "/model").json()
            ready = client.get(url + "/readyz")
            good = (ready.status_code == 200
                    and m.get("provider") == "ollama_proxy"
                    and m.get("model") == "qwen3:14b"
                    and m.get("routing_profile") == "unified"
                    and m.get("primary_route_v1_active") is True
                    and m.get("route_admission_mode") == "guarded"
                    and m.get("understanding_strategy") == "structured")
            add("preflight", "PASS" if good else "FAIL",
                provider=m.get("provider"), model=m.get("model"),
                unified=m.get("routing_profile") == "unified")
            if not good:
                save()
                return 2
            groups, latest, blocked, prior, tokens = {}, {}, set(), {}, {}
            for name, group, content, consent in CASES:
                if group in blocked:
                    add(name, "BLOCKED", failure="earlier_case_in_group")
                    continue
                if group not in groups:
                    response = client.post(url + "/conversations")
                    response.raise_for_status()
                    groups[group] = response.json()["id"]
                cid = groups[group]
                replay = name.endswith("_REPLAY")
                rid = latest.get(group) if replay else (
                    "p4e-unseen-" + uuid.uuid4().hex[:16])
                if not replay:
                    latest[group] = rid
                started = time.monotonic()
                with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                    db.row_factory = sqlite3.Row
                    before = {
                        t: sorted(read_rows(db, t, cid),
                                  key=lambda row: row.get("id", row.get("turn_id", "")))
                        for t in ("turns", "primary_route_evidence",
                                  "conversation_tasks", "agent_attempts",
                                  "pending_write_authorities",
                                  "conversation_plans")
                    }
                if name in {"PENDING_TASK_CONFIRM", "PENDING_TASK_REPLAY"}:
                    if group not in tokens:
                        add(name, "BLOCKED", failure="candidate_token_missing")
                        blocked.add(group)
                        continue
                    response = client.post(
                        url + f"/conversations/{cid}/pending-task/confirm",
                        json={**tokens[group], "client_request_id": rid})
                else:
                    response = client.post(
                        url + f"/conversations/{cid}/agent/messages",
                        json={"content": content, "client_request_id": rid,
                              "memory_write_consent": consent})
                body = response.json()
                tid = body.get("turn_id")
                evidence = None
                if tid and response.status_code == 200 and name != "PENDING_TASK_CONFIRM":
                    detail = client.get(
                        url + f"/conversations/{cid}/turns/{tid}/diagnostics")
                    if detail.status_code == 200:
                        evidence = detail.json().get("primary_route_evidence")
                try:
                    categories = json.loads(response.headers.get(
                        "X-HealthAgent-Model-Call-Categories", "{}"))
                except (TypeError, ValueError):
                    categories = {}
                if not isinstance(categories, dict):
                    categories = {}
                events = [
                    {"name": x.get("name"), "ok": x.get("ok")}
                    for x in (body.get("trace") or [])
                    if x.get("type") in {"tool", "clarification_resumed"}
                ]
                with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                    db.row_factory = sqlite3.Row
                    if replay:
                        after = {
                            t: sorted(read_rows(db, t, cid),
                                      key=lambda row: row.get("id", row.get("turn_id", "")))
                            for t in before}
                        physical = {"unchanged": before == after}
                    else:
                        physical = physical_audit(db, cid, name, tid)
                plan = body.get("plan") or []
                attempts = ("0" if name in {"PENDING_TASK_CONFIRM", "PENDING_TASK_REPLAY"}
                            else response.headers.get("X-HealthAgent-Model-HTTP-Attempts"))
                ok = assess(name, response.status_code, body, evidence,
                            categories, attempts, events, plan, prior, physical)
                if name in {"PLAN_CREATE_A", "PLAN_CREATE_B"} and ok:
                    prior[group] = {"plan": plan}
                if name == "PENDING_TASK_ANSWER" and ok:
                    tokens[group] = body.get("confirmation")
                add(name, "PASS" if ok else "FAIL",
                    http_status=response.status_code,
                    status=body.get("status"),
                    termination_reason=body.get("termination_reason"),
                    route_kind=(evidence or {}).get("route_kind"),
                    disposition=(evidence or {}).get("disposition"),
                    admission_reason=(evidence or {}).get("admission_reason"),
                    refusal_code=body.get("primary_v1_refusal_code"),
                    validation_issues=body.get("primary_v1_validation_issues", []),
                    semantic_attempts=(evidence or {}).get("semantic_attempts"),
                    model_categories=categories,
                    model_http_attempts=attempts,
                    tool_events=events,
                    plan_statuses=[step.get("status") for step in plan],
                    physical=physical,
                    duration_s=round(time.monotonic() - started, 2))
                if not ok:
                    blocked.add(group)
        save()
        return 0 if report["verdict"] == "PASS" else 1
    except (httpx.HTTPError, OSError, sqlite3.Error, ValueError,
            TypeError, KeyError) as exc:
        report["error_type"] = type(exc).__name__
        save()
        return 2


if __name__ == "__main__":
    sys.exit(main())
