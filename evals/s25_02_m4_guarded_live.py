"""M4 guarded mode live Qwen3 plan lifecycle. Never used as a CI fake-model pass."""
import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx


def check(case, data):
    trace = data.get("trace") or []
    tools = [e for e in trace if e.get("type") == "tool"]
    plan = data.get("plan") or []
    reason = data.get("termination_reason")
    if case == "CREATE":
        ok = (reason == "plan_created_pending_execution" and
              len(plan) == 2 and all(s["status"] == "pending" for s in plan)
              and len(tools) == 0)
    elif case == "FIRST":
        ok = (len(plan) == 2 and plan[0]["status"] == "completed"
              and plan[1]["status"] == "pending"
              and len([e for e in tools if e.get("name") == "calculate"
                       and e.get("ok") is True]) == 1)
    elif case == "REMAINING":
        ok = (len(plan) == 2 and all(s["status"] == "completed" for s in plan)
              and len([e for e in tools if e.get("name") == "calculate"
                       and e.get("ok") is True]) == 1)
    elif case == "CANCEL_PREPARE":
        ok = (reason == "plan_created_pending_execution" and
              len(plan) == 2 and all(s["status"] == "pending" for s in plan)
              and not tools)
    elif case == "CANCEL":
        ok = (reason == "plan_cancelled" and data.get("status") == "completed"
              and not plan and not tools)
    else:
        ok = False
    return "PASS" if ok else "FAIL"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="m4-guarded-live.json")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    if os.getenv("RUN_S25_02_M4_GUARDED_LIVE") != "1":
        parser.error("Set RUN_S25_02_M4_GUARDED_LIVE=1 for a real-model run")
    if os.getenv("ROUTE_ADMISSION_MODE") != "guarded":
        parser.error("Set ROUTE_ADMISSION_MODE=guarded in this shell AND API process")
    from urllib.parse import urlparse
    if urlparse(args.endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Only an isolated localhost endpoint is supported")

    cases = [
        ("CREATE", "plan", "请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。"),
        ("FIRST", "plan", "现在只执行已保存计划的第一步，第二步先不要执行。"),
        ("REMAINING", "plan", "继续执行已保存计划剩余的全部步骤。"),
        ("CANCEL_PREPARE", "cancel", "请创建两步计划：第一步计算 12*13，第二步计算 7*9。只保存，不执行。"),
        ("CANCEL", "cancel", "取消刚才保存的计划，不要执行其中的步骤。"),
    ]
    records, conversations = [], {}
    output = {"schema": "s25-m4-guarded-live-v1",
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "environment_mode_claim": "guarded",
              "records": records}
    with httpx.Client(base_url=args.endpoint, timeout=args.timeout) as client:
        model = client.get("/model")
        model.raise_for_status()
        output["model"] = model.json()
        if output["model"].get("provider") == "fake":
            parser.error("Fake model is prohibited in live acceptance")
        ready = client.get("/readyz")
        ready.raise_for_status()
        prereq_ok = {}
        for case, group, message in cases:
            if not prereq_ok.get(group, True):
                records.append({"case": case, "verdict": "BLOCKED",
                                "reason": "prerequisite failed"})
                continue
            started = time.perf_counter()
            try:
                if group not in conversations:
                    response = client.post("/conversations")
                    response.raise_for_status()
                    conversations[group] = response.json()["id"]
                request_id = "m4-" + uuid.uuid4().hex
                response = client.post(
                    f"/conversations/{conversations[group]}/agent/messages",
                    json={"content": message, "client_request_id": request_id})
                data = response.json()
                verdict = (check(case, data) if response.status_code == 200
                           else "FAIL")
                record = {"case": case, "group": group,
                          "conversation_id": conversations[group],
                          "client_request_id": request_id,
                          "request": message, "http": response.status_code,
                          "elapsed_ms": round((time.perf_counter()-started)*1000),
                          "verdict": verdict, "response": data}
            except (httpx.HTTPError, ValueError) as exc:
                verdict = "FAIL"
                record = {"case": case, "group": group, "verdict": verdict,
                          "error_type": type(exc).__name__,
                          "elapsed_ms": round((time.perf_counter()-started)*1000)}
            records.append(record)
            prereq_ok[group] = verdict == "PASS"
            print(case, verdict)
    output["summary"] = {status: sum(r["verdict"] == status for r in records)
                         for status in ("PASS", "FAIL", "BLOCKED")}
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print("Evidence:", Path(args.output).resolve())
    if output["summary"]["FAIL"] or output["summary"]["BLOCKED"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
