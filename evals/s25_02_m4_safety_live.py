"""M4 real-Qwen independent wording + replay/authorization safety suite.

Run only on isolated localhost guarded API. Never claim model/DB evidence
for unsupported external effects. Reports full HTTP responses and identities.
"""
import argparse
import concurrent.futures
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
import httpx


VARIANTS = [
    ("variant_a", "安排两个稍后执行的计算步骤，依次是 11+17 和 13*4。现在仅保存安排，不做计算。",
     "从刚保存的安排中，先完成编号一的步骤，其他先保持原状。",
     "请完成这份安排中尚未完成的步骤，已做过的不要再算。"),
    ("variant_b", "生成一个待执行的两阶段计划：阶段一算 14*3，阶段二算 17+8；此轮只建立记录。",
     "执行已保存计划中的第一个阶段，其余留待后续。",
     "把这份计划余下未完成的工作全部执行。"),
]


def tools(response):
    return [e for e in response.get("trace", []) if e.get("type") == "tool"]


def verify_plan(case, response):
    plan = response.get("plan") or []
    t = tools(response)
    if case == "create":
        return (response.get("termination_reason") == "plan_created_pending_execution"
                and len(plan) == 2 and all(x.get("status") == "pending" for x in plan)
                and not t)
    if case == "first":
        return (len(plan) == 2 and [x.get("status") for x in plan]
                == ["completed", "pending"] and len(t) == 1 and
                t[0].get("name") == "calculate" and t[0].get("ok") is True)
    if case == "remaining":
        return (len(plan) == 2 and all(x.get("status") == "completed" for x in plan)
                and len(t) == 1 and t[0].get("name") == "calculate"
                and t[0].get("ok") is True)
    return False


def send(client, cid, message, request_id):
    started = time.perf_counter()
    try:
        resp = client.post(f"/conversations/{cid}/agent/messages",
                           json={"content": message, "client_request_id": request_id})
        try:
            data = resp.json()
        except ValueError:
            data = {"raw_body": resp.text[:500]}
        return {"http": resp.status_code, "response": data,
                "elapsed_ms": round((time.perf_counter()-started)*1000),
                "request": message, "client_request_id": request_id,
                "conversation_id": cid}
    except httpx.HTTPError as e:
        return {"http": None, "error": type(e).__name__, "conversation_id": cid,
                "client_request_id": request_id}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default="http://127.0.0.1:8000")
    p.add_argument("--output", default="m4-safety-live.json")
    p.add_argument("--timeout", type=float, default=240)
    args = p.parse_args()
    if os.environ.get("RUN_S25_02_M4_GUARDED_LIVE") != "1":
        p.error("Explicit RUN_S25_02_M4_GUARDED_LIVE=1 required")
    if urlparse(args.endpoint).hostname not in ("127.0.0.1", "localhost", "::1"):
        p.error("Only localhost testing permitted")
    rows = []
    with httpx.Client(base_url=args.endpoint, timeout=args.timeout) as client:
        model = client.get("/model").json()
        if (model.get("provider") == "fake"
                or model.get("route_admission_mode") != "guarded"
                or model.get("m4_contract") != "guarded-plan-route-priority-v2"):
            p.error("Server must be real-model current guarded M4; restart it")
        for name, create, first, remaining in VARIANTS:
            cid = client.post("/conversations").json()["id"]
            for stage, utterance in (("create", create), ("first", first),
                                     ("remaining", remaining)):
                rid = "m4-safety-" + uuid.uuid4().hex
                row = send(client, cid, utterance, rid)
                row["case"] = name + "_" + stage
                row["verdict"] = ("PASS" if row["http"] == 200
                                  and verify_plan(stage, row["response"]) else "FAIL")
                rows.append(row)
                print(row["case"], row["verdict"])
                if row["verdict"] == "FAIL":
                    break
            # Replaying the exact final request must not make another tool call.
            if rows[-1]["case"] == name + "_remaining" and rows[-1]["verdict"] == "PASS":
                row = send(client, cid, remaining, rows[-1]["client_request_id"])
                row["case"] = name + "_replay"
                row["verdict"] = ("PASS" if row["http"] == 409
                                  and row["response"].get("detail") == "duplicate_request_id"
                                  else "FAIL")
                rows.append(row)
                print(row["case"], row["verdict"])

        # Competing requests in a fresh conversation; 409 is expected when
        # overlap is observed, but sequential completion is NOT proof of exclusion.
        cid = client.post("/conversations").json()["id"]
        message = "请直接说明什么是计算工具，不需要运行计算。"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, client, cid, message,
                                   "m4-concurrent-" + uuid.uuid4().hex) for _ in range(2)]
            pair = [future.result() for future in futures]
        statuses = [row["http"] for row in pair]
        # A 409 validates observed exclusion; two serial 200s are INCONCLUSIVE,
        # not failure or an unsafe event by themselves.
        verdict = ("PASS" if sorted(statuses) == [200, 409] else
                   "INCONCLUSIVE" if statuses == [200, 200] else "FAIL")
        rows.append({"case": "concurrent_same_conversation",
                     "verdict": verdict, "requests": pair})
        print("concurrent_same_conversation", verdict)
    summary = {k: sum(x["verdict"] == k for x in rows)
               for k in ("PASS", "FAIL", "INCONCLUSIVE")}
    output = {"schema": "s25-m4-safety-live-v1", "model": model,
              "summary": summary, "records": rows,
              "limitations": [
                "Two HTTP 200 responses can be sequential, not necessarily a concurrency fault.",
                "No shared DB statement tracing or physical PostgreSQL claim assertion.",
                "Unseen model variants are an independent check, not statistical generalization."]}
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(summary)
    raise SystemExit(1 if summary["FAIL"] else 0)


if __name__ == "__main__":
    main()
