"""Final-head real Qwen verification of Pending task write lifecycle.

Separate from the frozen first-run 19-case suite. All input is synthetic.
Runs against local API with a fresh disposable SQLite. No database mutations
are performed by this verifier. The API owns every business effect.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import httpx

from evals.primary_wp_c_phase4_e_unseen import LEGACY_CATEGORIES, read_rows

GROUPS = (
    ("valid", "我想新增一个待办，但是还没想好标题，请先向我确认。",
     "检查虚构仪器的标定标签", "create"),
    ("switch", "准备新增一项待办，任务名称暂时没有定，请先问我。",
     "我们换个话题，简单介绍一下模型上下文协议是什么。", "abandon"),
    ("cancel", "帮我准备一个待办，尚未确定名称，先询问我标题。",
     "取消刚才创建待办的请求，不要新建任务。", "abandon"),
    ("missing", "请先问我一个待办的名称，尚未决定，不要立刻创建。",
     "继续执行此前保存的计划，我没有提供新任务名称。", "abandon"),
)


def rows(db, table, cid):
    return read_rows(db, table, cid)


def audit(db, cid, turn_id):
    turn = [r for r in rows(db, "turns", cid) if r["id"] == turn_id]
    ev = [r for r in rows(db, "primary_route_evidence", cid)
          if r["turn_id"] == turn_id]
    auth = rows(db, "pending_write_authorities", cid)
    tasks = rows(db, "conversation_tasks", cid)
    attempts = rows(db, "agent_attempts", cid)
    return {
        "turn_evidence_ok": (
            len(turn) == 1 and len(ev) == 1
            and ev[0]["schema_version"] == "primary-route-v1"
            and ev[0]["semantic_attempts"] == 1),
        "authority_statuses": sorted(a["status"] for a in auth),
        "authority_unconsumed": all(a["consumed_turn_id"] is None for a in auth),
        "task_count": len(tasks),
        "attempt_count": len(attempts),
        "attempt_tools": sorted(a["tool_name"] for a in attempts),
        "attempt_ok": all(a["status"] == "succeeded" for a in attempts),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8012")
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--output",
                        default="artifacts/wp-c/phase4-e-final-live-v1.json")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    endpoint = urlparse(args.endpoint)
    if (endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "localhost"}
            or not endpoint.port or endpoint.username or endpoint.password
            or endpoint.path not in {"", "/"}):
        parser.error("local HTTP endpoint with explicit port required")
    dbpath = args.sqlite.resolve()
    if not dbpath.is_file():
        parser.error("isolated SQLite missing; start API with exact DATABASE_URL first")
    with sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("SELECT 1 FROM primary_route_evidence LIMIT 1")
        # This verifier is intentionally single-use on an empty disposable DB.
        if db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] != 0:
            parser.error("database has existing Turns; use a fresh isolated SQLite")

    output = Path(args.output)
    if output.exists():
        parser.error("report exists; use a new output filename, do not overwrite evidence")
    report = {
        "schema_version": "phase4-e-final-live-v1",
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True).strip(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "known_final_head_qwen_pending_lifecycle",
        "cases": [], "verdict": "FAIL",
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def record(case, ok, **info):
        report["cases"].append({
            "case": case, "verdict": "PASS" if ok else "FAIL", **info})
        print(case, "PASS" if ok else "FAIL", flush=True)

    def persist():
        count = len(report["cases"])
        passed = sum(x["verdict"] == "PASS" for x in report["cases"])
        report["summary"] = {"PASS": passed, "FAIL": count - passed}
        report["verdict"] = ("PASS" if count == 1 + 2 * len(GROUPS)
                             and count == passed else "FAIL")
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print("REPORT", output.resolve(), report["verdict"], flush=True)

    try:
        with httpx.Client(timeout=args.timeout, trust_env=False) as client:
            base = args.endpoint.rstrip("/")
            model = client.get(base + "/model").json()
            ready = client.get(base + "/readyz")
            good = (
                ready.status_code == 200
                and model.get("provider") == "ollama_proxy"
                and model.get("model") == "qwen3:14b"
                and model.get("routing_profile") == "unified"
                and model.get("primary_route_v1_active") is True
                and model.get("route_admission_mode") == "guarded"
                and model.get("understanding_strategy") == "structured")
            record("preflight", good,
                   model=model.get("model"),
                   provider=model.get("provider"),
                   unified=model.get("routing_profile"))
            if not good:
                persist()
                return 2

            for group, origin, followup, mode in GROUPS:
                cid = client.post(base + "/conversations").json()["id"]
                blocked = False
                for stage, message in (("origin", origin), ("followup", followup)):
                    name = f"{group}_{stage}"
                    if blocked:
                        record(name, False, reason="preceding_stage_failed")
                        continue
                    rid = "p4e-final-" + uuid.uuid4().hex
                    response = client.post(
                        base + f"/conversations/{cid}/agent/messages",
                        json={"content": message, "client_request_id": rid,
                              "memory_write_consent": False})
                    body = response.json()
                    tid = body.get("turn_id")
                    detail = (client.get(base +
                              f"/conversations/{cid}/turns/{tid}/diagnostics")
                              if response.status_code == 200 and tid else None)
                    ev = ((detail.json().get("primary_route_evidence") or {})
                          if detail is not None and detail.status_code == 200 else {})
                    try:
                        cats = json.loads(response.headers.get(
                            "X-HealthAgent-Model-Call-Categories", "{}"))
                    except (ValueError, TypeError):
                        cats = {}
                    if not isinstance(cats, dict):
                        cats = {}
                    with sqlite3.connect(
                            dbpath.as_uri() + "?mode=ro", uri=True) as db:
                        db.row_factory = sqlite3.Row
                        physical = audit(db, cid, tid)
                    tools = [t.get("name") for t in (body.get("trace") or [])
                             if t.get("type") == "tool"]
                    core = (
                        response.status_code == 200
                        and physical["turn_evidence_ok"]
                        and ev.get("semantic_attempts") == 1
                        and cats.get("primary") == 1
                        and not any(cats.get(c, 0) for c in LEGACY_CATEGORIES)
                    )
                    if stage == "origin":
                        ok = (core and ev.get("route_kind") == "clarify"
                              and body.get("termination_reason") == "needs_input"
                              and physical["authority_statuses"] == ["pending"]
                              and physical["task_count"] == 0
                              and physical["attempt_count"] == 0 and not tools)
                    elif mode == "create":
                        ok = (
                            core and ev.get("route_kind") == "clarify_response"
                            and body.get("termination_reason") == "pending_task_created"
                            and physical["authority_statuses"] == ["consumed"]
                            and physical["task_count"] == 1
                            and physical["attempt_count"] == 1
                            and physical["attempt_tools"] == ["create_task"]
                            and physical["attempt_ok"] and tools == ["create_task"])
                    else:
                        ok = (
                            core and ev.get("route_kind") != "clarify_response"
                            and body.get("termination_reason") != "pending_task_created"
                            and physical["authority_statuses"] == ["revoked"]
                            and physical["authority_unconsumed"]
                            and physical["task_count"] == 0
                            and physical["attempt_count"] == 0 and not tools)
                    record(name, ok, http_status=response.status_code,
                           route_kind=ev.get("route_kind"),
                           disposition=ev.get("disposition"),
                           termination_reason=body.get("termination_reason"),
                           semantic_attempts=ev.get("semantic_attempts"),
                           model_categories=cats,
                           tool_names=tools,
                           physical=physical)
                    if not ok:
                        blocked = True
        persist()
        return 0 if report["verdict"] == "PASS" else 1
    except (httpx.HTTPError, OSError, sqlite3.Error, TypeError, ValueError,
            KeyError) as exc:
        report["error_type"] = type(exc).__name__
        persist()
        return 2


if __name__ == "__main__":
    sys.exit(main())
