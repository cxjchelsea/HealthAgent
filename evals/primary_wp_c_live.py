"""WP-C phase 1: independent real-provider HTTP and durable evidence smoke.

This runner NEVER stubs the model, tool, Host, or database. It requires a
running localhost HealthAgent HTTP server with the real Unified Primary route.
Each scenario uses a new conversation and fresh request id. The output keeps
only bounded metadata, synthetic inputs and safe counts; no credentials.
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

CREATE = ("请创建一个两步可执行计划：第一步计算 3+4，"
          "第二步计算 8+9。只保存，不执行。")
CALC = "请使用计算工具计算 (19+23)*4，返回数值结果，不要创建计划。"
MISSING = "请继续执行之前保存的计划剩余步骤。"


def _git_head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _categories(headers):
    try:
        value = json.loads(headers.get("X-HealthAgent-Model-Call-Categories", "{}"))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8012")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--output", default="artifacts/wp-c/phase1-live.json")
    parser.add_argument("--diagnostic-only", action="store_true",
                        help="Single real calculation request + preflight; capture safe refusal code")
    args = parser.parse_args()

    endpoint = args.endpoint.rstrip("/")
    if not (endpoint.startswith("http://127.0.0.1:")
            or endpoint.startswith("http://localhost:")):
        parser.error("WP-C accepts only local HTTP endpoint")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": "wp-c-live-phase1-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_head": _git_head(), "endpoint": endpoint,
        "real_model": True, "scenarios": [], "passed": False,
    }

    def record(case, passed, **details):
        row = {"case": case, "passed": bool(passed), **details}
        result["scenarios"].append(row)
        print(("[PASS] " if passed else "[FAIL] ") + case +
              " " + json.dumps(details, ensure_ascii=False)[:400], flush=True)

    def persist():
        result["passed"] = (
            len(result["scenarios"]) == (2 if args.diagnostic_only else 4)
            and all(x["passed"] for x in result["scenarios"]))
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print("EVIDENCE " + str(output.resolve()), flush=True)

    try:
        with httpx.Client(timeout=args.timeout, trust_env=False) as client:
            health = client.get(endpoint + "/healthz")
            ready = client.get(endpoint + "/readyz")
            model = client.get(endpoint + "/model")
            if health.status_code != 200 or ready.status_code != 200 or model.status_code != 200:
                record("preflight", False, health=health.status_code,
                       ready=ready.status_code, model=model.status_code)
                persist()
                return 2
            info = model.json()
            expected = (
                info.get("provider") == "ollama_proxy"
                and info.get("routing_profile") == "unified"
                and info.get("primary_route_v1_active") is True
                and info.get("route_admission_mode") == "guarded"
                and info.get("understanding_strategy") == "structured"
                and info.get("configured") is True
                and ready.json().get("status") == "ready"
            )
            record("preflight", expected,
                   provider=info.get("provider"),
                   model=info.get("model"),
                   routing_profile=info.get("routing_profile"),
                   source=info.get("routing_profile_source"),
                   primary_active=info.get("primary_route_v1_active"))
            if not expected:
                persist()
                return 2

            def send(cid, prompt, request_id):
                started = time.perf_counter()
                response = client.post(
                    f"{endpoint}/conversations/{cid}/agent/messages",
                    json={"content": prompt, "client_request_id": request_id})
                elapsed = round(time.perf_counter() - started, 2)
                data = response.json()
                detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
                turn_id = data.get("turn_id") or detail.get("turn_id")
                diag = None
                if turn_id:
                    diag_response = client.get(
                        f"{endpoint}/conversations/{cid}/turns/{turn_id}/diagnostics")
                    if diag_response.status_code == 200:
                        diag = diag_response.json().get("primary_route_evidence")
                trace = data.get("trace") or []
                return {
                    "http_status": response.status_code,
                    "status": data.get("status") or detail.get("status"),
                    "reason": data.get("termination_reason") or detail.get("code"),
                    # Returned by the existing Primary handler as an internal,
                    # fixed-category error code; never capture raw LLM output.
                    "primary_refusal_code": data.get("primary_v1_refusal_code"),
                    "primary_validation_issues": data.get("primary_v1_validation_issues", []),
                    "turn_id": turn_id, "request_id": request_id,
                    "duration_s": elapsed, "categories": _categories(response.headers),
                    "model_http_attempts": response.headers.get(
                        "X-HealthAgent-Model-HTTP-Attempts"),
                    "tool_events": [
                        {"name": t.get("name"), "ok": t.get("ok")}
                        for t in trace if t.get("type") == "tool"],
                    "plan_statuses": [s.get("status") for s in (data.get("plan") or [])],
                    "answer_has_168": "168" in str(data.get("answer", "")),
                    "evidence": diag,
                }

            def fresh():
                resp = client.post(endpoint + "/conversations")
                resp.raise_for_status()
                return resp.json()["id"]

            # Independent conversations avoid a failed request polluting a
            # subsequent plan or pending checkpoint. No writes requested.
            calc_cid = fresh()
            calc = send(calc_cid, CALC, "wp-c-calc-" + uuid.uuid4().hex[:12])
            calc_ok = (
                calc["http_status"] == 200 and calc["status"] == "completed"
                and calc["answer_has_168"]
                and any(t == {"name": "calculate", "ok": True}
                        for t in calc["tool_events"])
                and calc["evidence"] is not None
                and calc["evidence"]["route_kind"] == "ordinary_tool"
                and calc["evidence"]["disposition"] == "allow"
                and calc["categories"].get("primary") == 1
                and sum(calc["categories"].get(k, 0) for k in
                        ("plan_intent", "plan_revision", "plan_cancellation",
                         "plan_execution_authorization",
                         "plan_execution_scope")) == 0)
            record("ordinary_calculate", calc_ok, **calc)
            if args.diagnostic_only:
                persist()
                return 0 if result["passed"] else 1

            missing_cid = fresh()
            missing = send(missing_cid, MISSING,
                           "wp-c-missing-" + uuid.uuid4().hex[:12])
            missing_ok = (
                missing["http_status"] == 200
                and missing["status"] == "partial"
                and not missing["tool_events"]
                and missing["evidence"] is not None
                and missing["evidence"]["route_kind"] == "plan_missing"
                and missing["evidence"]["disposition"] == "restrict"
                and missing["reason"] == "primary_v1_missing_plan_status"
                and missing["categories"].get("primary") == 1)
            record("missing_plan_no_dispatch", missing_ok, **missing)

            plan_cid = fresh()
            create = send(plan_cid, CREATE,
                          "wp-c-create-" + uuid.uuid4().hex[:12])
            create_ok = (
                create["http_status"] == 200
                and create["reason"] == "plan_created_pending_execution"
                and create["plan_statuses"] == ["pending", "pending"]
                and not create["tool_events"]
                and create["evidence"] is not None
                and create["evidence"]["route_kind"] == "plan_create"
                and create["evidence"]["disposition"] == "allow"
                and create["categories"].get("primary") == 1)
            record("create_plan_only", create_ok, **create)
            result["created_plan_conversation_id"] = plan_cid

        persist()
        return 0 if result["passed"] else 1
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        result["error_category"] = type(exc).__name__
        result["passed"] = False
        persist()
        print("WP-C transport or decoding error: " + type(exc).__name__,
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
