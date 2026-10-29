"""M5 unified live API acceptance. Requires an isolated real-model localhost API.

No fake-model, monkeypatch or synthetic execution can count as a live PASS.
All cases use fresh conversations and exact request IDs. Report is not a
substitute for independent SQLite/PostgreSQL physical readback.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid
from urllib.parse import urlparse

import httpx

PLAN = "请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。"
FIRST = "现在只执行已保存计划的第一步，第二步先不要执行。"
RESUME = "继续执行已保存计划剩余的全部步骤。"
REVISE = "将已保存计划的第二步由计算 5*6 改为计算 7*8，第一步不变，不要执行。"
CANCEL = "取消当前保存的计划，不要执行任何步骤。"
ORDINARY = "请使用计算工具计算 19+23 的结果，不要创建计划。"
# Keep the pending scenario as a read-only question: never authorize writes.
PENDING = "请查询 MCP 参考资料，但我还没有提供查询词。"
PENDING_ANSWER = "RAG"


def tool_events(body):
    return [x for x in (body.get("trace") or []) if x.get("type") == "tool"]


def statuses(body):
    return [x.get("status") for x in (body.get("plan") or [])]


def expected(case, http_status, body, categories):
    if case in {"CREATE_A", "CREATE_B", "CREATE_C"}:
        ok = (http_status == 200
              and body.get("termination_reason") == "plan_created_pending_execution"
              and statuses(body) == ["pending", "pending"] and not tool_events(body))
    elif case == "FIRST":
        ev = tool_events(body)
        ok = (http_status == 200 and statuses(body) == ["completed", "pending"]
              and len(ev) == 1 and ev[0].get("name") == "calculate"
              and ev[0].get("ok") is True)
    elif case == "RESUME":
        ev = tool_events(body)
        ok = (http_status == 200 and statuses(body) == ["completed", "completed"]
              and len(ev) == 1 and ev[0].get("name") == "calculate"
              and ev[0].get("ok") is True)
    elif case == "REPLAY":
        ok = http_status == 409 and body.get("detail") == "duplicate_request_id"
    elif case == "REVISE":
        steps = body.get("plan") or []
        ok = (http_status == 200
              and body.get("termination_reason") == "plan_revised_pending_execution"
              and len(steps) == 2 and all(x.get("status") == "pending" for x in steps)
              and steps[0].get("expected_arguments", {}).get("expression") == "3*4"
              and steps[1].get("expected_arguments", {}).get("expression") == "7*8"
              and not tool_events(body))
    elif case == "CANCEL":
        ok = (http_status == 200 and body.get("termination_reason") == "plan_cancelled"
              and body.get("status") == "completed" and not tool_events(body)
              and not body.get("plan"))
    elif case == "ORDINARY":
        # Must not persist a plan; physical readback verifies this separately.
        # A tool execution trace is required for this explicitly tool-using request.
        ev = tool_events(body)
        ok = (http_status == 200 and body.get("status") in {"completed", "partial"}
              and any(x.get("name") == "calculate" and x.get("ok") is True for x in ev)
              and not body.get("plan_checkpoint"))
    elif case == "PENDING":
        ok = (http_status == 200 and body.get("status") == "partial"
              and body.get("termination_reason") == "needs_input"
              and not tool_events(body))
    elif case == "PENDING_ANSWER":
        ok = (http_status == 200
              and body.get("termination_reason") == "verified_clarification_execution"
              and body.get("status") == "completed")
    else:
        ok = False
    if case not in {"REPLAY", "PENDING_ANSWER"}:
        # Exactly one M5 primary model route in the unified opt-in path.
        # This is NOT a claim of exactly one total model HTTP call.
        ok = ok and isinstance(categories, dict) and categories.get("primary") == 1
        ok = ok and categories.get("plan_intent", 0) == 0
    return bool(ok)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default="http://127.0.0.1:8012")
    p.add_argument("--output", default="m5-unified-live.json")
    p.add_argument("--timeout", type=float, default=240)
    args = p.parse_args()
    if os.getenv("RUN_S25_02_M5_UNIFIED_LIVE") != "1":
        p.error("Explicitly set RUN_S25_02_M5_UNIFIED_LIVE=1")
    if (os.getenv("ROUTE_ADMISSION_MODE") != "guarded"
            or os.getenv("M5_UNIFIED_ROUTE_ENABLED", "").lower() != "true"
            or os.getenv("UNDERSTANDING_STRATEGY") != "structured"):
        p.error("Guarded, structured and M5_UNIFIED_ROUTE_ENABLED=true required in both shells")
    if urlparse(args.endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        p.error("Only isolated localhost is accepted")
    cases = [
        ("CREATE_A", "lifecycle", PLAN),
        ("FIRST", "lifecycle", FIRST),
        ("RESUME", "lifecycle", RESUME),
        ("REPLAY", "lifecycle", RESUME),
        ("CREATE_B", "revision", PLAN),
        ("REVISE", "revision", REVISE),
        ("CREATE_C", "cancel", PLAN),
        ("CANCEL", "cancel", CANCEL),
        ("ORDINARY", "ordinary", ORDINARY),
        ("PENDING", "pending", PENDING),
        ("PENDING_ANSWER", "pending", PENDING_ANSWER),
    ]
    out = {
        "schema": "s25-m5-unified-live-v1",
        "run_id": uuid.uuid4().hex[:12],
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "records": [], "conversations": {}, "verdict": "BLOCKED",
    }
    with httpx.Client(base_url=args.endpoint, timeout=args.timeout) as client:
        model = client.get("/model")
        model.raise_for_status()
        out["model"] = model.json()
        if out["model"].get("provider") in {None, "fake"}:
            p.error("Real model required")
        if out["model"].get("route_admission_mode") != "guarded":
            p.error("API does not advertise guarded routing")
        client.get("/readyz").raise_for_status()
        failed_groups = set()
        last_ids = {}
        for case, group, prompt in cases:
            if group in failed_groups:
                out["records"].append({"case": case, "group": group,
                                       "verdict": "BLOCKED",
                                       "reason": "prior_case_failed"})
                continue
            if group not in out["conversations"]:
                r = client.post("/conversations")
                r.raise_for_status()
                out["conversations"][group] = r.json()["id"]
            cid = out["conversations"][group]
            rid = (last_ids[group] if case == "REPLAY"
                   else "m5-unified-" + out["run_id"] + "-" + uuid.uuid4().hex[:10])
            if case != "REPLAY":
                last_ids[group] = rid
            started = time.monotonic()
            try:
                response = client.post(
                    f"/conversations/{cid}/agent/messages",
                    json={"content": prompt, "client_request_id": rid})
                payload = response.json()
                head = response.headers.get("x-healthagent-model-call-categories")
                try:
                    categories = json.loads(head) if head else None
                except (ValueError, TypeError):
                    categories = None
                ok = expected(case, response.status_code, payload, categories)
                record = {
                    "case": case, "group": group, "conversation_id": cid,
                    "client_request_id": rid, "http": response.status_code,
                    "elapsed_ms": round((time.monotonic()-started)*1000),
                    "model_http_attempts":
                        response.headers.get("x-healthagent-model-http-attempts"),
                    "model_call_categories": categories,
                    "response": payload, "verdict": "PASS" if ok else "FAIL",
                }
            except (httpx.HTTPError, ValueError) as exc:
                record = {"case": case, "group": group, "conversation_id": cid,
                          "client_request_id": rid, "verdict": "FAIL",
                          "error_type": type(exc).__name__}
            out["records"].append(record)
            if record["verdict"] != "PASS":
                failed_groups.add(group)
            print(case, record["verdict"])
    out["summary"] = {
        k: sum(x["verdict"] == k for x in out["records"])
        for k in ("PASS", "FAIL", "BLOCKED")
    }
    out["verdict"] = ("PASS" if out["summary"] == {
        "PASS": len(cases), "FAIL": 0, "BLOCKED": 0} else "FAIL")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    print(json.dumps({"verdict": out["verdict"], "summary": out["summary"],
                      "output": str(Path(args.output).resolve())}, ensure_ascii=False))
    raise SystemExit(0 if out["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
