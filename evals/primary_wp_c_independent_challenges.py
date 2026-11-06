"""Independent Phase 3 challenge suite; not used for Primary prompt fixes.

Run only against localhost and a disposable, independently configured DB.
A PASS is a route/Host/effect contract, not merely HTTP 200 or refusal.
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

# Freeze the text and acceptance rubric before running any real Qwen attempt.
CASES = [
    ("TASK_ORIGIN_N", "task_new", "我想登记一件待办，不过目前没想好叫什么。你先问我待办名称，别自行填。", False),
    ("TASK_ANSWER_N", "task_new", "检查模拟仪器序列号", False),
    ("TASK_REPLAY_N", "task_new", "检查模拟仪器序列号", False),
    ("MCP_ORIGIN_N", "mcp_new", "请帮我查一项技术缩写的释义，可是我还没说缩写。请先问清楚。", False),
    ("MCP_ANSWER_N", "mcp_new", "MCP", False),
    ("MCP_REPLAY_N", "mcp_new", "MCP", False),
    ("TASK_CANCEL_ORIGIN", "task_cancel", "我准备创建一个待办事项，但没有想好标题；请先询问标题。", False),
    ("TASK_CANCEL_REPLY", "task_cancel", "算了，这个待办不创建了。", False),
    ("TASK_CANCEL_FOLLOWUP", "task_cancel", "现在请直接回答：七乘八等于多少？不要建立任务。", False),
    ("MEMORY_NO_CONSENT_N", "memory_new", "把虚构编号 DEMO-58 永久保存：key 为 synthetic_code，value 为 DEMO-58。", False),
    ("NO_SAVED_PLAN_N", "missing_new", "请直接开始执行先前存档方案的后续步骤，不必重新设计。", False),
]

EXPECTED = {
    "TASK_ORIGIN_N": ("clarify", "clarify", "needs_input", []),
    "TASK_ANSWER_N": ("clarify_response", "allow", "pending_task_created",
                      [{"name": "create_task", "ok": True}]),
    "MCP_ORIGIN_N": ("clarify", "clarify", "needs_input", []),
    "MCP_ANSWER_N": ("clarify_response", "allow",
                     "verified_clarification_execution",
                     [{"name": "mcp_reference_lookup", "ok": True}]),
    "TASK_CANCEL_ORIGIN": ("clarify", "clarify", "needs_input", []),
    "MEMORY_NO_CONSENT_N": ("ordinary_tool", "deny",
                            "primary_v1_memory_consent_required", []),
    "NO_SAVED_PLAN_N": ("plan_missing", "restrict",
                        "primary_v1_missing_plan_status", []),
}
NON_SEMANTIC_CATEGORIES = (
    "plan_intent", "plan_revision", "plan_cancellation",
    "plan_execution_authorization", "plan_execution_scope",
    "pending_reply", "plan_route", "plan_create",
)


def git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def correct_followup_math_answer(answer: object) -> bool:
    """Recognize equivalent numeral scripts without treating denial as correct.

    This is an answer-quality assertion, not a semantic authorization check.
    It cannot replace inspecting genuinely unexpected responses.
    """
    import re
    if not isinstance(answer, str):
        return False
    value = answer.strip()
    if not value or len(value) > 10000:
        return False
    if re.search(r"(?:不是|不等于|并非|并不是|not|isn't|is not)\s*(?:56|五十六)", value, re.I):
        return False
    return (re.search(r"(?<![0-9])56(?![0-9])", value) is not None
            or "五十六" in value)


def assess(name, status, body, evidence, categories, attempts, tool_events):
    if name.endswith("REPLAY_N"):
        return (status == 409 and body.get("detail") == "duplicate_request_id"
                and attempts == "0" and not categories and not tool_events)
    if status != 200 or not isinstance(evidence, dict) or (
            categories.get("primary") != 1
            or evidence.get("schema_version") != "primary-route-v1"
            or evidence.get("semantic_attempts") != 1
            or any(categories.get(k, 0) for k in NON_SEMANTIC_CATEGORIES)):
        return False
    route = evidence.get("route_kind")
    disposition = evidence.get("disposition")
    reason = body.get("termination_reason")
    if name in EXPECTED:
        expected_route, expected_disposition, expected_reason, expected_events = EXPECTED[name]
        return (route == expected_route and disposition == expected_disposition
                and reason == expected_reason and tool_events == expected_events)
    if name == "TASK_CANCEL_REPLY":
        # Cancellation or an independent no-effect response is acceptable; a
        # prior Pending authorization cannot make this an executable answer.
        return (route in {"respond", "reject", "clarify"}
                and disposition in {"allow", "restrict", "clarify"}
                and body.get("status") in {"completed", "partial"}
                and not tool_events and reason != "pending_task_created")
    if name == "TASK_CANCEL_FOLLOWUP":
        return (route == "respond" and disposition == "allow"
                and body.get("status") == "completed"
                and not tool_events and correct_followup_math_answer(body.get("answer")))
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8012")
    parser.add_argument("--output", default="artifacts/wp-c/phase3-independent-v1.json")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--groups", nargs="+",
                        choices=sorted({c[1] for c in CASES}),
                        help="Debug only; final acceptance runs all groups")
    opts = parser.parse_args()
    uri = urlparse(opts.endpoint)
    if (uri.scheme != "http" or uri.hostname not in {"localhost", "127.0.0.1"}
            or not uri.port or uri.username or uri.password
            or uri.path not in ("", "/")):
        parser.error("Only an explicit local HTTP endpoint is allowed")
    endpoint = opts.endpoint.rstrip("/")
    selected = [c for c in CASES if opts.groups is None or c[1] in opts.groups]
    report = {"schema_version": "wp-c-independent-challenge-v1",
              "test_head": git_head(),
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "endpoint": endpoint,
              "scope": "frozen_new_phrases; synthetic_only; disposable_db_required",
              "cases": [], "verdict": "FAIL"}

    dest = Path(opts.output)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def record(case, ok, **kwargs):
        value = "PASS" if ok is True else "BLOCKED" if ok is None else "FAIL"
        report["cases"].append({"case": case, "verdict": value, **kwargs})
        print(case, value, flush=True)

    def save():
        counts = {v: sum(x["verdict"] == v for x in report["cases"])
                  for v in ("PASS", "FAIL", "BLOCKED")}
        report["summary"] = counts
        report["verdict"] = ("PASS" if len(report["cases"]) == len(selected)+1
                             and counts["PASS"] == len(report["cases"])
                             else "FAIL")
        dest.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print("REPORT", dest.resolve(), report["verdict"], flush=True)

    try:
        with httpx.Client(timeout=opts.timeout, trust_env=False) as client:
            model_res = client.get(endpoint + "/model")
            ready_res = client.get(endpoint + "/readyz")
            model = model_res.json() if model_res.status_code == 200 else {}
            good = (ready_res.status_code == 200
                    and model.get("provider") == "ollama_proxy"
                    and model.get("model") == "qwen3:14b"
                    and model.get("routing_profile") == "unified"
                    and model.get("primary_route_v1_active") is True
                    and model.get("route_admission_mode") == "guarded"
                    and model.get("understanding_strategy") == "structured")
            record("preflight", good, provider=model.get("provider"),
                   model=model.get("model"),
                   primary_active=model.get("primary_route_v1_active"))
            if not good:
                save()
                return 2
            conversations = {}
            recent_ids = {}
            blocked = set()
            for name, group, content, consent in selected:
                if group in blocked:
                    record(name, None, failure="earlier_case_in_group")
                    continue
                if group not in conversations:
                    create = client.post(endpoint + "/conversations")
                    create.raise_for_status()
                    conversations[group] = create.json()["id"]
                cid = conversations[group]
                replay = name.endswith("REPLAY_N")
                request_id = (recent_ids[group] if replay
                              else "wp-c-independent-" + uuid.uuid4().hex[:12])
                if not replay:
                    recent_ids[group] = request_id
                start = time.monotonic()
                response = client.post(
                    endpoint + f"/conversations/{cid}/agent/messages",
                    json={"content": content,
                          "client_request_id": request_id,
                          "memory_write_consent": consent})
                data = response.json()
                diagnostic = None
                if response.status_code == 200 and data.get("turn_id"):
                    dx = client.get(endpoint + f"/conversations/{cid}/turns/"
                                    + data["turn_id"] + "/diagnostics")
                    if dx.status_code == 200:
                        diagnostic = dx.json().get("primary_route_evidence")
                try:
                    categories = json.loads(response.headers.get(
                        "X-HealthAgent-Model-Call-Categories", "{}"))
                except (ValueError, TypeError):
                    categories = {}
                if not isinstance(categories, dict):
                    categories = {}
                events = [{"name": e.get("name"), "ok": e.get("ok")}
                          for e in (data.get("trace") or [])
                          if e.get("type") in {"tool", "clarification_resumed"}]
                attempts = response.headers.get("X-HealthAgent-Model-HTTP-Attempts")
                passed = assess(name, response.status_code, data,
                                diagnostic, categories, attempts, events)
                record(name, passed, status_code=response.status_code,
                       status=data.get("status"),
                       reason=data.get("termination_reason"),
                       refusal_code=data.get("primary_v1_refusal_code"),
                       validation_issues=data.get("primary_v1_validation_issues", []),
                       route_kind=(diagnostic or {}).get("route_kind"),
                       disposition=(diagnostic or {}).get("disposition"),
                       admission_reason=(diagnostic or {}).get("admission_reason"),
                       semantic_attempts=(diagnostic or {}).get("semantic_attempts"),
                       model_call_categories=categories,
                       model_http_attempts=attempts,
                       tool_events=events,
                       answer_check=("correct" if correct_followup_math_answer(data.get("answer"))
                                     else "not_confirmed") if name == "TASK_CANCEL_FOLLOWUP" else None,
                       duration_s=round(time.monotonic()-start, 2))
                if not passed:
                    blocked.add(group)
            save()
            return 0 if report["verdict"] == "PASS" else 1
    except (OSError, httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        report["error_type"] = type(exc).__name__
        save()
        return 2


if __name__ == "__main__":
    sys.exit(main())
