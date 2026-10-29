"""M5 FIRST scoped live HTTP acceptance; requires a separately started real Qwen API.

This does not claim that CI mock execution is real model evidence.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx


def first_oracle(payload):
    plan = payload.get("plan") or []
    trace = payload.get("trace") or []
    tools = [x for x in trace if x.get("type") == "tool"]
    return (payload.get("status") == "partial"
            and len(plan) == 2
            and [x.get("status") for x in plan] == ["completed", "pending"]
            and len(tools) == 1
            and tools[0].get("name") == "calculate"
            and tools[0].get("ok") is True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8012")
    parser.add_argument("--output", default="m5-first-live.json")
    args = parser.parse_args()
    if os.environ.get("RUN_S25_02_M5_FIRST_LIVE") != "1":
        parser.error("Set RUN_S25_02_M5_FIRST_LIVE=1 explicitly")
    if os.environ.get("M5_FIRST_ROUTE_ENABLED") != "true":
        parser.error("Set M5_FIRST_ROUTE_ENABLED=true in shell AND running API")
    if os.environ.get("ROUTE_ADMISSION_MODE") != "guarded":
        parser.error("Set ROUTE_ADMISSION_MODE=guarded in shell AND running API")
    from urllib.parse import urlparse
    if urlparse(args.endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Only isolated localhost endpoints are supported")
    cases = [
        ("CREATE", "请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。"),
        ("FIRST", "现在只执行已保存计划的第一步，第二步先不要执行。"),
        ("REPLAY", "现在只执行已保存计划的第一步，第二步先不要执行。"),
    ]
    report = {"schema": "s25-m5-first-live-v1", "run_id": uuid.uuid4().hex[:12],
              "records": [], "verdict": "BLOCKED"}
    with httpx.Client(base_url=args.endpoint, timeout=240) as client:
        model = client.get("/model")
        model.raise_for_status()
        report["model"] = model.json()
        if report["model"].get("provider") == "fake":
            parser.error("Real model is mandatory")
        cid = client.post("/conversations").json()["id"]
        report["conversation_id"] = cid
        for name, prompt in cases:
            if name == "REPLAY" and report["records"][-1]["verdict"] != "PASS":
                break
            request_id = ("m5-first-" + report["run_id"] + "-first"
                          if name in {"FIRST", "REPLAY"} else
                          "m5-first-" + report["run_id"] + "-create")
            started = time.monotonic()
            response = client.post(
                f"/conversations/{cid}/agent/messages",
                json={"content": prompt, "client_request_id": request_id})
            body = response.json()
            category_header = response.headers.get("x-healthagent-model-call-categories")
            try:
                categories = json.loads(category_header) if category_header else None
            except ValueError:
                categories = None
            if name == "CREATE":
                plan = body.get("plan") or []
                passed = (response.status_code == 200 and len(plan) == 2
                          and all(x.get("status") == "pending" for x in plan)
                          and not [x for x in body.get("trace", []) if x.get("type") == "tool"])
            elif name == "FIRST":
                passed = response.status_code == 200 and first_oracle(body)
                passed = passed and categories is not None and categories.get("primary") == 1
                passed = passed and categories.get("plan_intent", 0) == 0
            else:
                passed = response.status_code == 409 and body.get("detail") == "duplicate_request_id"
            report["records"].append({
                "case": name, "status_code": response.status_code,
                "elapsed_ms": round((time.monotonic()-started)*1000, 1),
                "client_request_id": request_id, "response": body,
                "model_http_attempts": response.headers.get("x-healthagent-model-http-attempts"),
                "model_call_categories": categories,
                "verdict": "PASS" if passed else "FAIL"})
            if not passed:
                break
    report["verdict"] = ("PASS" if len(report["records"]) == 3
                         and all(row["verdict"] == "PASS" for row in report["records"])
                         else "FAIL")
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "output": args.output,
                      "records": len(report["records"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
