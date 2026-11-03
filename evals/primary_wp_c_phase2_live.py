"""WP-C Phase 2: real Qwen Primary plan lifecycle acceptance.

No fake model, mock, forced primary route or inferred execution authority.
A failure blocks dependent operations in that plan group. Safe metadata only.
Phase 2 does not simulate crashes, assert database contents or replace Phase 3/4.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx

CREATE_A = "请创建两步可执行计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。"
FIRST = "现在只执行已保存计划的第一步，第二步先不要执行。"
RESUME = "继续执行已保存计划剩余的全部步骤。"
CREATE_B = "请创建两步可执行计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。"
REVISE = "将已保存计划的第二步由计算 5*6 改为计算 7*8，第一步不变，不要执行。"
CREATE_C = "请创建两步可执行计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。"
CANCEL = "取消当前保存的计划，不要执行任何步骤。"

CASES = [
    ("CREATE_A", "execute", CREATE_A),
    ("FIRST", "execute", FIRST),
    ("RESUME", "execute", RESUME),
    ("REPLAY", "execute", RESUME),
    ("CREATE_B", "revise", CREATE_B),
    ("REVISE", "revise", REVISE),
    ("CREATE_C", "cancel", CREATE_C),
    ("CANCEL", "cancel", CANCEL),
]


def head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default="http://127.0.0.1:8012")
    p.add_argument("--output", default="artifacts/wp-c/phase2-live.json")
    p.add_argument("--timeout", type=float, default=240)
    selector = p.add_mutually_exclusive_group()
    selector.add_argument("--revision-only", action="store_true",
                          help="Run fresh CREATE_B -> REVISE only")
    selector.add_argument("--execution-only", action="store_true",
                          help="Run fresh CREATE_A -> FIRST -> RESUME -> REPLAY only")
    args = p.parse_args()
    selected_group = ("revise" if args.revision_only else
                      "execute" if args.execution_only else None)
    cases = [x for x in CASES if x[1] == selected_group] if selected_group else CASES
    url = args.endpoint.rstrip("/")
    if not url.startswith(("http://127.0.0.1:", "http://localhost:")):
        p.error("Only localhost acceptance endpoint is supported")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "wp-c-live-phase2-v1",
        "head": head(), "created_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": url, "cases": [], "verdict": "FAIL", "conversations": {},
    }

    def emit(case, ok, **extra):
        report["cases"].append({"case": case, "verdict": "PASS" if ok else "FAIL", **extra})
        print(case, report["cases"][-1]["verdict"], flush=True)

    def blocked(case, reason):
        report["cases"].append({"case": case, "verdict": "BLOCKED", "reason": reason})
        print(case, "BLOCKED", reason, flush=True)

    def save():
        counts = {k: sum(x["verdict"] == k for x in report["cases"])
                  for k in ("PASS", "FAIL", "BLOCKED")}
        report["summary"] = counts
        report["verdict"] = "PASS" if (
            len(report["cases"]) == len(cases) + 1
            and counts == {"PASS": len(cases) + 1, "FAIL": 0, "BLOCKED": 0}
        ) else "FAIL"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print("EVIDENCE", str(output.resolve()), report["verdict"], flush=True)

    try:
        with httpx.Client(timeout=args.timeout, trust_env=False) as client:
            info = client.get(url + "/model").json()
            ready = client.get(url + "/readyz")
            preflight = (
                ready.status_code == 200
                and info.get("provider") == "ollama_proxy"
                and info.get("model") == "qwen3:14b"
                and info.get("routing_profile") == "unified"
                and info.get("primary_route_v1_active") is True
                and info.get("route_admission_mode") == "guarded"
                and info.get("understanding_strategy") == "structured")
            emit("preflight", preflight,
                 model=info.get("model"), provider=info.get("provider"),
                 route=info.get("routing_profile"),
                 primary=info.get("primary_route_v1_active"))
            if not preflight:
                save()
                return 2

            groups, blocked_groups, last_rids = {}, set(), {}
            for case, group, message in cases:
                if group in blocked_groups:
                    blocked(case, "prior_group_failure")
                    continue
                if group not in groups:
                    created = client.post(url + "/conversations")
                    created.raise_for_status()
                    groups[group] = created.json()["id"]
                    report["conversations"][group] = groups[group]
                cid = groups[group]
                rid = (last_rids[group] if case == "REPLAY" else
                       "wp-c-p2-" + uuid.uuid4().hex[:15])
                if case != "REPLAY":
                    last_rids[group] = rid
                started = time.monotonic()
                response = client.post(
                    url + f"/conversations/{cid}/agent/messages",
                    json={"content": message, "client_request_id": rid})
                body = response.json()
                diag = None
                turn_id = body.get("turn_id")
                if turn_id and response.status_code == 200:
                    d = client.get(
                        url + f"/conversations/{cid}/turns/{turn_id}/diagnostics")
                    if d.status_code == 200:
                        diag = d.json().get("primary_route_evidence")
                cats = json.loads(
                    response.headers.get("X-HealthAgent-Model-Call-Categories", "{}"))
                tools = [{"name": t.get("name"), "ok": t.get("ok")}
                         for t in (body.get("trace") or [])
                         if t.get("type") == "tool"]
                plan = body.get("plan") or []
                states = [step.get("status") for step in plan]
                kind = {"CREATE_A": "plan_create", "CREATE_B": "plan_create",
                        "CREATE_C": "plan_create", "FIRST": "plan_execute_first",
                        "RESUME": "plan_resume", "REVISE": "plan_revise",
                        "CANCEL": "plan_cancel"}.get(case)
                primary_ok = (
                    isinstance(diag, dict)
                    and diag.get("schema_version") == "primary-route-v1"
                    and diag.get("route_kind") == kind
                    and diag.get("disposition") == "allow"
                    and diag.get("semantic_attempts") == 1
                    and cats.get("primary") == 1
                    and not any(cats.get(name, 0) for name in
                                ("plan_intent", "plan_revision", "plan_cancellation",
                                 "plan_execution_authorization",
                                 "plan_execution_scope")))
                if case in {"CREATE_A", "CREATE_B", "CREATE_C"}:
                    passed = (response.status_code == 200 and primary_ok
                              and body.get("termination_reason") ==
                              "plan_created_pending_execution"
                              and states == ["pending", "pending"] and not tools)
                elif case == "FIRST":
                    passed = (
                        response.status_code == 200 and primary_ok
                        and states == ["completed", "pending"]
                        and tools == [{"name": "calculate", "ok": True}])
                elif case == "RESUME":
                    passed = (
                        response.status_code == 200 and primary_ok
                        and states == ["completed", "completed"]
                        and tools == [{"name": "calculate", "ok": True}])
                elif case == "REVISE":
                    passed = (
                        response.status_code == 200 and primary_ok
                        and body.get("termination_reason") ==
                        "plan_revised_pending_execution"
                        and states == ["pending", "pending"] and not tools
                        and len(plan) == 2
                        and plan[0].get("expected_arguments", {}).get("expression") == "3*4"
                        and plan[1].get("expected_arguments", {}).get("expression") == "7*8")
                elif case == "CANCEL":
                    passed = (
                        response.status_code == 200 and primary_ok
                        and body.get("status") == "completed"
                        and body.get("termination_reason") == "plan_cancelled"
                        and not plan and not tools)
                else:
                    passed = (response.status_code == 409
                              and body.get("detail") == "duplicate_request_id"
                              and not tools and cats.get("primary", 0) == 0)
                emit(case, passed, http_status=response.status_code,
                     status=body.get("status"), reason=body.get("termination_reason"),
                     refusal_code=body.get("primary_v1_refusal_code"),
                     validation_issues=body.get("primary_v1_validation_issues", []),
                     route_triple=body.get("primary_v1_route_triple"),
                     duration_s=round(time.monotonic()-started, 2),
                     turn_id=turn_id, request_id=rid,
                     plan_statuses=states,
                     plan_arguments=[s.get("expected_arguments") for s in plan],
                     tools=tools, categories=cats,
                     model_http_attempts=response.headers.get(
                         "X-HealthAgent-Model-HTTP-Attempts"),
                     evidence=diag)
                if not passed:
                    blocked_groups.add(group)
        save()
        return 0 if report["verdict"] == "PASS" else 1
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        report["error_category"] = type(exc).__name__
        save()
        print("WP-C Phase 2 failure:", type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
