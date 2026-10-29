"""Paired real-model M4 comparison, opt-in and localhost only.

Run legacy and guarded servers independently with equivalent model settings
and separate databases. Tracks HTTP wall time and observable shadow cost;
does NOT fabricate core model-call counts. Produces evidence, not a verdict
from insufficient samples.
"""
import argparse
import json
import os
import statistics
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

PROMPTS = (
    "请创建两步计划：第一步计算 3*4，第二步计算 5*6。只保存，不执行。",
    "现在只执行已保存计划的第一步，第二步先不要执行。",
    "继续执行已保存计划剩余的全部步骤。",
)


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered)-1)*p
    lo = int(index)
    hi = min(lo+1, len(ordered)-1)
    return round(ordered[lo] + (ordered[hi]-ordered[lo])*(index-lo), 1)


def request(client, path, body=None):
    start = time.perf_counter()
    response = client.post(path, json=body)
    ms = round((time.perf_counter()-start)*1000)
    try:
        data = response.json()
    except ValueError:
        data = {"error": "non_json_response"}
    return {"http": response.status_code, "elapsed_ms": ms, "response": data}


def assess(records):
    if len(records) != 3 or any(r["http"] != 200 for r in records):
        return False
    a,b,c = (r["response"] for r in records)
    return (a.get("termination_reason") == "plan_created_pending_execution"
            and b.get("plan") is not None
            and [x.get("status") for x in b["plan"]] == ["completed", "pending"]
            and c.get("plan") is not None
            and [x.get("status") for x in c["plan"]] == ["completed", "completed"]
            and sum(t.get("type") == "tool" and t.get("ok") is True for t in b.get("trace", [])) == 1
            and sum(t.get("type") == "tool" and t.get("ok") is True for t in c.get("trace", [])) == 1)


def run_mode(endpoint, expected_mode, count, timeout):
    with httpx.Client(base_url=endpoint, timeout=timeout) as client:
        model = client.get("/model").json()
        if (model.get("route_admission_mode") != expected_mode
                or model.get("provider") == "fake"):
            raise ValueError("wrong_model_or_route_mode_" + expected_mode)
        cases = []
        for index in range(count):
            cid = client.post("/conversations").json()["id"]
            records = []
            for prompt in PROMPTS:
                records.append(request(client, f"/conversations/{cid}/agent/messages",
                                       {"content": prompt,
                                        "client_request_id": uuid.uuid4().hex}))
                if records[-1]["http"] != 200:
                    break
            cases.append({"index": index, "conversation_id": cid,
                          "functional_pass": assess(records),
                          "requests": records})
            print(expected_mode, index, "PASS" if cases[-1]["functional_pass"] else "FAIL")
        elapsed = [r["elapsed_ms"] for case in cases for r in case["requests"]]
        shadow_elapsed = [r["response"]["route_shadow"].get("diagnostic_elapsed_ms")
                          for case in cases for r in case["requests"]
                          if isinstance(r["response"].get("route_shadow"), dict)]
        shadow_calls = [r["response"]["route_shadow"].get("diagnostic_model_calls")
                        for case in cases for r in case["requests"]
                        if isinstance(r["response"].get("route_shadow"), dict)]
        return {"model": model, "cases": cases,
                "functional_pass": sum(c["functional_pass"] for c in cases),
                "sample_requests": len(elapsed),
                "latency_ms": {"p50": percentile(elapsed, .5),
                               "p95": percentile(elapsed, .95),
                               "mean": round(statistics.mean(elapsed),1) if elapsed else None},
                "shadow_diagnostic_ms": {"p50": percentile(shadow_elapsed,.5),
                                          "p95": percentile(shadow_elapsed,.95)},
                "shadow_diagnostic_model_calls": {
                    "observed_total": sum(c for c in shadow_calls if isinstance(c,int)),
                    "count_observed": sum(isinstance(c,int) for c in shadow_calls)},
                "core_model_calls": "NOT_INSTRUMENTED"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--legacy-endpoint", required=True)
    p.add_argument("--guarded-endpoint", required=True)
    p.add_argument("--cycles", type=int, default=3)
    p.add_argument("--timeout", type=float, default=240)
    p.add_argument("--output", default="m4-paired-comparison.json")
    a = p.parse_args()
    if os.getenv("RUN_S25_02_M4_PAIRED_LIVE") != "1":
        p.error("RUN_S25_02_M4_PAIRED_LIVE=1 required")
    if not 1 <= a.cycles <= 20:
        p.error("--cycles must be 1..20")
    if any(urlsplit(endpoint).hostname not in ("127.0.0.1", "localhost")
           for endpoint in (a.legacy_endpoint, a.guarded_endpoint)):
        p.error("only isolated localhost endpoints supported")
    if a.legacy_endpoint == a.guarded_endpoint:
        p.error("legacy and guarded endpoints must differ")
    # Alternating mode order reduces persistent warm-up ordering bias.
    result = {"schema": "s25-m4-paired-performance-v1", "cycles": a.cycles,
              "limitations": [
                  "Different isolated databases and identical Qwen model settings required.",
                  "HTTP wall time includes queueing and any Shadow diagnostics.",
                  "Core provider model-call count is not instrumented.",
                  "Small cycle count cannot establish stable performance percentiles.",
                  "External concurrency and exactly-once safety are outside this benchmark."]}
    for mode, endpoint in (("legacy",a.legacy_endpoint),
                           ("guarded",a.guarded_endpoint)):
        result[mode] = run_mode(endpoint, mode, a.cycles, a.timeout)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("legacy",result["legacy"]["latency_ms"],"guarded",result["guarded"]["latency_ms"])


if __name__ == "__main__":
    main()
