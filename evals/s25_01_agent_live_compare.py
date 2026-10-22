"""S25-01 REAL Agent Conversation API comparison, separate live deployments.

Start two API processes with the SAME model, tools and hardware:
  port 8001: UNDERSTANDING_STRATEGY=direct
  port 8002: UNDERSTANDING_STRATEGY=structured
Then, with real reachable Qwen provider:
  RUN_S25_01_LIVE=1 python -m evals.s25_01_agent_live_compare \
    --direct http://127.0.0.1:8001 --structured http://127.0.0.1:8002 \
    --output s25_01_agent_comparison.json

This is an end-to-end performance smoke comparator, NOT a claim of semantic
accuracy or equivalence. Each strategy uses its own new conversation.
"""
import argparse
import json
import os
import statistics
import time
import uuid
from pathlib import Path
import httpx
from agent.config import Settings

TASKS = (
    ("single", ["请解释 Agent Loop 的作用。"]),
    ("planning", ["帮我制定一个三项明日工作草案，但不要保存。"]),
    ("multiturn", ["帮我整理明天最多三项待办，只需草案。", "第一项提前，其他保持不变。"]),
    ("clarify", ["把第二项换掉。"]),
)

def percentile(values, percentile_value):
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile_value + .999) - 1))
    return ordered[index]

def run(endpoint, group, turns, timeout):
    entries = []
    with httpx.Client(base_url=endpoint, timeout=timeout) as client:
        create = client.post("/conversations")
        create.raise_for_status()
        conversation_id = create.json()["id"]
        for turn, content in enumerate(turns):
            begun = time.monotonic()
            try:
                response = client.post(
                    f"/conversations/{conversation_id}/agent/messages",
                    json={"content": content,
                          "client_request_id": f"s25-{uuid.uuid4().hex}"},
                )
                elapsed_ms = round((time.monotonic() - begun) * 1000)
                data = response.json()
                entries.append({
                    "group": group, "turn": turn + 1, "http_status": response.status_code,
                    "duration_ms": elapsed_ms,
                    "agent_status": data.get("status"),
                    "failure_code": (data.get("detail") or {}).get("code")
                                    if isinstance(data.get("detail"), dict) else None,
                    "decision_steps": data.get("steps"),
                    "trace_events": len(data.get("trace", [])),
                })
            except (httpx.HTTPError, ValueError) as exc:
                entries.append({"group": group, "turn": turn + 1,
                                "error": type(exc).__name__,
                                "duration_ms": round((time.monotonic() - begun) * 1000)})
    return entries

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct", required=True)
    parser.add_argument("--structured", required=True)
    parser.add_argument("--output", default="s25_01_agent_comparison.json")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if not Settings().run_s25_01_live:
        raise SystemExit("RUN_S25_01_LIVE=1 required; no synthetic measurements.")
    if args.direct.rstrip("/") == args.structured.rstrip("/"):
        raise SystemExit("Direct and structured APIs must use different endpoints.")
    results = {}
    for strategy, address in (("direct", args.direct), ("structured", args.structured)):
        rows = [entry for label, turns in TASKS
                for entry in run(address, label, turns, args.timeout)]
        durations = [r["duration_ms"] for r in rows]
        results[strategy] = {
            "rows": rows,
            "p50_ms": statistics.median(durations),
            "p95_ms": percentile(durations, .95),
            "http_errors": sum(r.get("http_status", 0) >= 400 or "error" in r for r in rows),
        }
    report = {
        "evidence": "real_live_Agent_API_requests_not_fakes",
        "strategies": results,
        "caveats": [
            "Not randomized or concurrency-controlled; hardware/model load affects timings.",
            "Statuses and timeouts are NOT a measure of semantic task correctness.",
            "Manual review of goal state, tool evidence, trace and permissions is required.",
            "This comparator alone cannot justify changing the default strategy.",
        ],
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(json.dumps({key: {"p50_ms": value["p50_ms"],
                            "p95_ms": value["p95_ms"],
                            "http_errors": value["http_errors"]}
                      for key, value in results.items()}, indent=2))

if __name__ == "__main__":
    main()
