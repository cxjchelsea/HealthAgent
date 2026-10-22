"""S25-01 one-command real model end-to-end smoke, no two Uvicorn servers needed.

PowerShell:
  $env:RUN_S25_01_LIVE="1"
  $env:MODEL_PROVIDER="ollama_proxy"
  $env:MODEL_BASE_URL="http://127.0.0.1:11434"
  $env:MODEL_NAME="qwen3:14b"
  python -m evals.s25_01_local_e2e --output s25_01_e2e.json

Runs production FastAPI routes with independent temporary SQLite DBs.
Real configured provider is used by BOTH Understanding and Agent Loop.
"""
import argparse
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from evals.s25_01_paths import report_path
from fastapi.testclient import TestClient
from agent.config import Settings
from apps.api.main import create_app

SAMPLES = (
    ("simple", ("请解释 Agent Loop 的作用。",)),
    ("task", ("计算 8*8，告诉我结果。",)),
    ("plan", ("请给明天拟一份最多三项的安排草案，不要保存。",)),
    ("multiturn", ("列三项明天的任务草案，不保存。", "请修改第二项，其余保留。")),
)

def percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, min(len(ordered)-1, int(len(ordered)*pct + .999)-1))]

def exercise(settings):
    details = []
    with TestClient(create_app(settings)) as client:
        for group, turns in SAMPLES:
            created = client.post("/conversations")
            created.raise_for_status()
            cid = created.json()["id"]
            for index, utterance in enumerate(turns):
                start = time.perf_counter()
                try:
                    if index and details[-1].get("case") == group and details[-1].get("http_status") != 200:
                        details.append({"case": group, "turn": index+1,
                                        "elapsed_ms": 0, "status": "blocked_by_prior_failure",
                                        "code": "prior_turn_failed", "skipped": True})
                        continue
                    response = client.post(f"/conversations/{cid}/agent/messages", json={
                        "content": utterance,
                        "client_request_id": f"live-{settings.understanding_strategy}-{group}-{index:03d}",
                    })
                    payload = response.json()
                    details.append({
                        "case": group, "turn": index+1,
                        "elapsed_ms": round((time.perf_counter()-start)*1000),
                        "http_status": response.status_code,
                        "status": payload.get("status"),
                        "code": payload.get("detail", {}).get("code") if isinstance(payload.get("detail"), dict) else None,
                        "step_count": payload.get("steps"),
                        "trace_count": len(payload.get("trace", [])),
                        "trace_summary": [
                            {"type": event.get("type"), "step": event.get("step"),
                             "tool": event.get("name"), "ok": event.get("ok")}
                            for event in payload.get("trace", [])[-20:]
                            if event.get("type") in {"decision", "tool", "final",
                                "repeat_tool_stop", "step_limit", "partial_first_step_verified"}
                        ],
                        "plan_progress": (
                            {"total": len(payload["plan"]),
                             "completed": sum(step.get("status") == "completed"
                                              for step in payload["plan"])}
                            if isinstance(payload.get("plan"), list) else None),
                        "failure_trace": (payload.get("detail", {}).get("trace_summary", [])
                                          if isinstance(payload.get("detail"), dict) else []),
                    })
                except Exception as exc:
                    # Failures must remain visible; never silently count as successes.
                    details.append({"case": group, "turn": index+1,
                                    "elapsed_ms": round((time.perf_counter()-start)*1000),
                                    "error": type(exc).__name__})
    durations = [r["elapsed_ms"] for r in details]
    return {"rows": details, "p50_ms": statistics.median(durations),
            "p95_ms": percentile(durations, .95),
            "request_failures": sum(not r.get("skipped") and ("error" in r or r.get("http_status") != 200) for r in details),
            "blocked_followups": sum(bool(r.get("skipped")) for r in details)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="s25_01_e2e.json")
    args = parser.parse_args()
    base = Settings()
    if not base.run_s25_01_live:
        raise SystemExit("RUN_S25_01_LIVE=1 required; this is not a mocked evaluation.")
    if base.model_provider not in ("ollama_proxy", "openai_compatible"):
        raise SystemExit("MODEL_PROVIDER must specify a real model provider.")
    if base.model_provider == "openai_compatible" and not base.model_api_key:
        raise SystemExit("MODEL_API_KEY missing.")
    report = {"evidence": "real-model-calls-via-production-conversation-api",
              "provider": base.model_provider, "agent_model": base.model_name,
              "light_model": base.understanding_light_model,
              "strong_model": base.understanding_strong_model,
              "strategies": {},
              "limitations": [
                  "Sequential small smoke matrix, not statistically stable latency.",
                  "Does not certify semantic correctness, no-side-effects, or release readiness.",
                  "No raw private request/response content saved; manual review required.",
                  "Run separate warmup/cold runs before changing production defaults.",
              ]}
    with tempfile.TemporaryDirectory(prefix="s25-01-live-") as root:
        for strategy in ("direct", "structured"):
            settings = Settings(
                database_url="sqlite:///" + str(Path(root) / (strategy+".db")),
                understanding_strategy=strategy,
            )
            report["strategies"][strategy] = exercise(settings)
    report_path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: {label: v[label] for label in ("p50_ms", "p95_ms", "request_failures")}
                      for k, v in report["strategies"].items()}, ensure_ascii=False, indent=2))
    print("Saved:", args.output)

if __name__ == "__main__":
    main()
