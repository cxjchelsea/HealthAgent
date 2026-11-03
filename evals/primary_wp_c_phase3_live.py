"""WP-C Phase 3: real-model boundary and Pending challenge evaluation.

Safe synthetic tasks only, isolated local API/database required.
No external side-effectful operations beyond local synthetic tasks/memory.
This suite must not rewrite model output or accept generic refusals as successes.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import httpx

# Fresh challenges: do NOT incorporate failures verbatim into the model prompt.
CASES = [
    # Existing plan must not hijack a fresh independent tool request.
    ("PLAN_SETUP", "plan_ordinary", "请拟定两步计算计划：先算 13+17，再算 21+24。仅保存，暂不运行。", False),
    ("ORDINARY_WITH_PLAN", "plan_ordinary",
     "暂时保留计划不动；请另用计算工具求 (27-9)*3 的值，直接告诉我答案。", False),
    ("MISSING_REFERENCE", "unknown", "接着执行我昨天保存的计算计划，先不要新建。", False),
    ("TASK_WRITE", "task", "给当前会话新增一个任务，标题是“整理模拟评测纪要”。", False),
    ("TASK_REPLAY", "task", "给当前会话新增一个任务，标题是“整理模拟评测纪要”。", False),
    ("MEMORY_DENY", "memory_denied",
     "请长期记住：我的虚构评测代号是 SAMPLE-42，键为 sample_eval_code，值为 SAMPLE-42。", False),
    ("MEMORY_ALLOW", "memory_allowed",
     "请长期记住：我的虚构评测代号是 SAMPLE-43，键为 sample_eval_code，值为 SAMPLE-43。", True),
    ("PENDING_TASK_ORIGIN", "pending_task",
     "替我建立一个待办事项，不过我还没有决定标题，请先问我名称。", False),
    ("PENDING_TASK_ANSWER", "pending_task", "整理虚构实验设备清单", False),
    ("PENDING_TASK_REPLAY", "pending_task", "整理虚构实验设备清单", False),
    ("PENDING_MCP_ORIGIN", "pending_mcp",
     "请查 MCP 术语参考资料，我忘了告诉你要查询的术语，先问我。", False),
    ("PENDING_MCP_ANSWER", "pending_mcp", "RAG", False),
    ("PENDING_MCP_REPLAY", "pending_mcp", "RAG", False),
]

EXPECTED_ROUTES = {
    "PLAN_SETUP": ("plan_create", "allow"),
    "ORDINARY_WITH_PLAN": ("ordinary_tool", "allow"),
    "MISSING_REFERENCE": ("plan_missing", "restrict"),
    "TASK_WRITE": ("ordinary_tool", "allow"),
    "MEMORY_DENY": ("ordinary_tool", "deny"),
    "MEMORY_ALLOW": ("ordinary_tool", "allow"),
    "PENDING_TASK_ORIGIN": ("clarify", "clarify"),
    "PENDING_TASK_ANSWER": ("clarify_response", "allow"),
    "PENDING_MCP_ORIGIN": ("clarify", "clarify"),
    "PENDING_MCP_ANSWER": ("clarify_response", "allow"),
}

LEGACY_CATEGORIES = (
    "plan_intent", "plan_revision", "plan_cancellation",
    "plan_execution_authorization", "plan_execution_scope",
    "pending_reply", "plan_route", "plan_create",
)


def head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def assess(case, status, data, evidence, categories, tools, *, original_plan=None):
    """Strict route/effect contract, not just a 'no exception' smoke test."""
    if case.endswith("_REPLAY"):
        return (status == 409 and data.get("detail") == "duplicate_request_id"
                and not tools and not categories.get("primary", 0))

    expected = EXPECTED_ROUTES[case]
    primary_ok = (
        status == 200 and categories.get("primary") == 1
        and not any(categories.get(k, 0) for k in LEGACY_CATEGORIES)
        and isinstance(evidence, dict)
        and evidence.get("schema_version") == "primary-route-v1"
        and evidence.get("semantic_attempts") == 1
        and (evidence.get("route_kind"), evidence.get("disposition")) == expected)
    if not primary_ok:
        return False
    reason = data.get("termination_reason")
    if case == "PLAN_SETUP":
        return (reason == "plan_created_pending_execution"
                and len(data.get("plan") or []) == 2
                and all(x.get("status") == "pending" for x in data["plan"])
                and not tools)
    if case == "ORDINARY_WITH_PLAN":
        return (data.get("status") == "completed"
                and any(t == {"name": "calculate", "ok": True} for t in tools)
                and "54" in str(data.get("answer", ""))
                and not data.get("plan_checkpoint")
                and not any(t.get("name") in ("create_task", "save_memory")
                            for t in tools))
    if case == "MISSING_REFERENCE":
        return reason == "primary_v1_missing_plan_status" and not tools
    if case == "TASK_WRITE":
        return (data.get("status") == "completed"
                and reason == "primary_v1_task_created"
                and tools == [{"name": "create_task", "ok": True}])
    if case == "MEMORY_DENY":
        return reason == "primary_v1_memory_consent_required" and not tools
    if case == "MEMORY_ALLOW":
        return (data.get("status") == "completed"
                and reason == "primary_v1_memory_saved"
                and tools == [{"name": "save_memory", "ok": True}])
    if case in {"PENDING_TASK_ORIGIN", "PENDING_MCP_ORIGIN"}:
        return (data.get("status") == "partial"
                and reason == "needs_input" and not tools)
    if case == "PENDING_TASK_ANSWER":
        return (data.get("status") == "completed"
                and reason == "pending_task_created"
                and tools == [{"name": "create_task", "ok": True}])
    if case == "PENDING_MCP_ANSWER":
        return (data.get("status") == "completed"
                and reason == "verified_clarification_execution"
                and tools == [{"name": "mcp_reference_lookup", "ok": True}])
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint", default="http://127.0.0.1:8012")
    p.add_argument("--output", default="artifacts/wp-c/phase3-live.json")
    p.add_argument("--timeout", type=float, default=240)
    p.add_argument("--groups", nargs="+",
                   choices=sorted(set(x[1] for x in CASES)),
                   help="Select independent scenario groups; default all")
    args = p.parse_args()
    url = args.endpoint.rstrip("/")
    parsed = urlparse(url)
    if (parsed.scheme != "http" or parsed.hostname not in
            {"localhost", "127.0.0.1"} or not parsed.port
            or parsed.username or parsed.password or parsed.path not in ("", "/")):
        p.error("Only explicit local http://127.0.0.1:PORT is permitted")
    cases = [c for c in CASES if args.groups is None or c[1] in args.groups]
    report = {
        "schema_version": "wp-c-live-phase3-v1",
        "head": head(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": url,
        "safety": "synthetic_tasks_only; isolated_database_required",
        "cases": [], "conversations": {}, "verdict": "FAIL",
    }
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def add(case, verdict, **details):
        report["cases"].append({"case": case, "verdict": verdict, **details})
        print(case, verdict, flush=True)

    def persist():
        counts = {k: sum(x["verdict"] == k for x in report["cases"])
                  for k in ("PASS", "FAIL", "BLOCKED")}
        report["summary"] = counts
        report["verdict"] = (
            "PASS" if len(report["cases"]) == 1 + len(cases)
            and counts == {"PASS": len(report["cases"]), "FAIL": 0,
                           "BLOCKED": 0} else "FAIL")
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REPORT", destination.resolve(), report["verdict"], flush=True)

    try:
        with httpx.Client(timeout=args.timeout, trust_env=False) as client:
            model_response = client.get(url + "/model")
            model_response.raise_for_status()
            model = model_response.json()
            ready = client.get(url + "/readyz")
            good = (
                ready.status_code == 200
                and model.get("provider") == "ollama_proxy"
                and model.get("model") == "qwen3:14b"
                and model.get("routing_profile") == "unified"
                and model.get("primary_route_v1_active") is True
                and model.get("route_admission_mode") == "guarded"
                and model.get("understanding_strategy") == "structured")
            add("preflight", "PASS" if good else "FAIL",
                provider=model.get("provider"), model=model.get("model"),
                routing_profile=model.get("routing_profile"),
                primary_active=model.get("primary_route_v1_active"))
            if not good:
                persist()
                return 2
            groups = {}
            blocked = set()
            request_ids = {}
            for case, group, message, consent in cases:
                if group in blocked:
                    add(case, "BLOCKED", category="prior_group_failed")
                    continue
                if group not in groups:
                    created = client.post(url + "/conversations")
                    created.raise_for_status()
                    groups[group] = created.json()["id"]
                    report["conversations"][group] = groups[group]
                cid = groups[group]
                replay = case.endswith("_REPLAY")
                rid = request_ids[group] if replay else (
                    "wp-c-p3-" + uuid.uuid4().hex[:15])
                if not replay:
                    request_ids[group] = rid
                start = time.monotonic()
                response = client.post(
                    url + f"/conversations/{cid}/agent/messages",
                    json={"content": message, "client_request_id": rid,
                          "memory_write_consent": consent})
                body = response.json()
                turn_id = body.get("turn_id")
                diagnostic = None
                if turn_id and response.status_code == 200:
                    d = client.get(
                        url + f"/conversations/{cid}/turns/{turn_id}/diagnostics")
                    if d.status_code == 200:
                        diagnostic = d.json().get("primary_route_evidence")
                try:
                    cats = json.loads(response.headers.get(
                        "X-HealthAgent-Model-Call-Categories", "{}"))
                except (TypeError, ValueError):
                    cats = {}
                if not isinstance(cats, dict):
                    cats = {}
                events = [{"name": t.get("name"), "ok": t.get("ok")}
                          for t in (body.get("trace") or [])
                          if t.get("type") in {"tool", "clarification_resumed"}]
                ok = assess(case, response.status_code, body, diagnostic,
                            cats, events)
                # Do not persist raw response/answer, free-form model outputs,
                # task title or memory values in the shareable report.
                add(case, "PASS" if ok else "FAIL",
                    http_status=response.status_code,
                    status=body.get("status"),
                    reason=body.get("termination_reason"),
                    refusal_code=body.get("primary_v1_refusal_code"),
                    validation_issues=body.get("primary_v1_validation_issues", []),
                    route_triple=body.get("primary_v1_route_triple"),
                    duration_s=round(time.monotonic()-start, 2),
                    turn_id=turn_id, request_id=rid,
                    tool_events=events,
                    plan_statuses=[s.get("status") for s in (body.get("plan") or [])],
                    categories=cats,
                    model_http_attempts=response.headers.get(
                        "X-HealthAgent-Model-HTTP-Attempts"),
                    evidence=diagnostic)
                if not ok:
                    blocked.add(group)
        persist()
        return 0 if report["verdict"] == "PASS" else 1
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError) as exc:
        report["error_category"] = type(exc).__name__
        persist()
        print("Phase 3 failed:", type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
