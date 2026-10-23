"""S25-02 Step 5 real-Qwen acceptance over the actual Conversation HTTP API.

Run against an isolated local API configured with a real model; never mock
Understanding or AgentLoop. Keeps failed cases and unknown semantic checks
visible instead of reporting an unearned PASS.
"""
import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


def tool_events(data, name):
    return [x for x in data.get("trace", [])
            if x.get("type") == "tool" and x.get("name") == name]


def check(case, data):
    if not isinstance(data, dict) or "trace" not in data:
        return "FAIL", "missing API trace"
    reason = data.get("termination_reason")
    if case == "F04_division_zero":
        events = tool_events(data, "calculate")
        return ("PASS" if events and any(x.get("ok") is False for x in events)
                else "FAIL", "calculator failure must be observed, not invented")
    if case == "F02_calculation":
        events = tool_events(data, "calculate")
        return ("PASS" if any(x.get("ok") is True for x in events)
                else "FAIL", "real calculator call required; verify 168 manually")
    if case == "F03_lookup":
        return ("PASS" if any(x.get("ok") is True for x in
                tool_events(data, "mcp_reference_lookup")) else "FAIL",
                "real MCP reference lookup required")
    if case == "F03_missing_term":
        return ("PASS" if data.get("status") == "partial"
                and not tool_events(data, "mcp_reference_lookup") else "FAIL",
                "missing term must clarify without lookup")
    if case == "F03_clarification_reply":
        return ("PASS" if any(x.get("ok") is True for x in
                tool_events(data, "mcp_reference_lookup"))
                or any(x.get("type") == "clarification_resumed"
                       and x.get("ok") for x in data.get("trace", []))
                else "FAIL", "reply must resume the real pending lookup")
    if case in {"D03_plan_create", "D03_plan_create_fresh"}:
        return ("PASS" if reason == "plan_created_pending_execution"
                and data.get("steps") == 0
                and len(data.get("plan", [])) == 2 else "FAIL",
                "formal plan must be saved but not executed")
    if case == "D05_plan_revision":
        plan = data.get("plan", [])
        return ("PASS" if reason == "plan_revised_pending_execution"
                and data.get("steps") == 0 and len(plan) == 2
                and plan[1].get("expected_arguments", {}).get(
                    "expression") == "9*9" else "FAIL",
                "only pending second step must change without execution")
    if case == "D03_execute_first":
        plan = data.get("plan", [])
        return ("PASS" if len(plan) == 2
                and plan[0].get("status") == "completed"
                and plan[1].get("status") == "pending" else "FAIL",
                "only first saved step may run")
    if case == "D06_resume":
        plan = data.get("plan", [])
        return ("PASS" if len(plan) == 2
                and all(x.get("status") == "completed" for x in plan)
                else "FAIL", "remaining saved steps must finish once")
    if case == "E06_standalone_with_plan":
        return ("PASS" if tool_events(data, "calculate")
                and not any(x.get("type") == "plan_resume_execution"
                            for x in data.get("trace", [])) else "FAIL",
                "standalone calculator must not execute saved plan")
    if case == "E06_unsaved_draft":
        return ("PASS" if not tool_events(data, "create_task")
                and data.get("plan_checkpoint") is None else "FAIL",
                "draft must not persist a task or formal plan")
    if case == "F04_unsupported_shell":
        attempted = [x for x in data.get("trace", []) if x.get("type") == "tool"]
        if attempted:
            return "FAIL", "unsupported shell request must execute no tools"
        if reason == "unsupported_capability" and data.get("steps") == 0:
            return "PASS", "structured unsupported tool refusal, no tool executed"
        return "REVIEW", "no tool executed; manually check model refusal wording"

    if case == "E03_task_create":
        return ("PASS" if any(x.get("ok") is True for x in
                tool_events(data, "create_task")) else "FAIL",
                "explicit task creation requires real tool evidence")
    return "REVIEW", "no automatic semantic oracle"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="s25_02_step5_live_acceptance.json")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    parsed = urlparse(args.endpoint)
    if os.getenv("RUN_S25_02_LIVE") != "1":
        parser.error("RUN_S25_02_LIVE=1 is required; this is a real-model suite")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Use an isolated local API; never target a production service")

    # Isolated conversations: no cross-case state leakage. Intentionally run
    # plan cases in one conversation; all other cases are independent.
    cases = [
        ("F02_calculation", "请使用计算工具计算 (19+23)*4，报告真实工具结果。", "calc"),
        ("F03_lookup", "请查询 MCP 中 RAG 的定义，只引用工具结果。", "lookup"),
        ("F03_missing_term", "请在 MCP 资料中查一个词条，但还没说词条。", "clarify"),
        ("F03_clarification_reply", "RAG", "clarify"),
        ("F04_division_zero", "请调用计算工具计算 12/(3-3)，准确报告执行失败。", "zero"),
        ("F04_unsupported_shell", "请使用 shell 执行 echo hello。", "shell"),
        ("E06_unsaved_draft", "拟定三项工作任务草案，不要保存也不要创建计划。", "draft"),
        ("E03_task_create", "请正式创建任务：周五提交测试报告。", "task"),
        ("D03_plan_create", "请建立一个暂不执行的两步正式计划：先查询 RAG，再计算 8*8。", "plan_probe"),
        ("E06_standalone_with_plan", "请单独使用计算工具计算 19+23，不要执行刚才的计划。", "plan_probe"),
        ("D03_plan_create_fresh", "请建立一个暂不执行的两步正式计划：先查询 RAG，再计算 8*8。", "plan"),
        ("D05_plan_revision", "请将刚才正式计划的第二步计算 8*8 修改成 9*9，其他不变，不执行。", "plan"),
        ("D03_execute_first", "现在只执行已保存计划的第一步，其余步骤先不执行。", "plan"),
        ("D06_resume", "继续执行已保存计划剩余的所有步骤。", "plan"),
    ]
    rows, conversations = [], {}
    last_by_group = {}
    started = datetime.now(timezone.utc).isoformat()
    with httpx.Client(base_url=args.endpoint, timeout=args.timeout) as client:
        model = client.get("/model")
        if model.status_code != 200:
            parser.error("API /model unavailable or requires authentication")
        metadata = model.json()
        if metadata.get("provider") == "fake":
            parser.error("API is using fake provider, not live Qwen")
        ready = client.get("/readyz")
        if ready.status_code != 200:
            parser.error("API not ready")
        for index, (case, utterance, group) in enumerate(cases):
            start = time.perf_counter()
            # A failed prerequisite invalidates subsequent plan-state
            # assertions, not evidence that the next action is correct.
            if (group == "plan" and case != "D03_plan_create_fresh"
                    and last_by_group.get(group) not in {"PASS", "REVIEW"}):
                rows.append({"case": case, "result": "BLOCKED",
                             "rationale": "prior saved-plan prerequisite failed"})
                print(case, "BLOCKED")
                continue
            try:
                if group not in conversations:
                    resp = client.post("/conversations")
                    resp.raise_for_status()
                    conversations[group] = resp.json()["id"]
                response = client.post(
                    f"/conversations/{conversations[group]}/agent/messages",
                    json={"content": utterance,
                          "client_request_id": f"step5-{index:02d}-{uuid.uuid4().hex[:12]}"})
                data = response.json()
                verdict, rationale = check(case, data) if response.status_code == 200 else (
                    "FAIL", "API returned non-200")
                rows.append({
                    "case": case, "http_status": response.status_code,
                    "status": data.get("status") if isinstance(data, dict) else None,
                    "termination_reason": data.get("termination_reason") if isinstance(data, dict) else None,
                    "failure_code": ((data.get("detail") or {}).get("code")
                                     if isinstance(data.get("detail"), dict) else None),
                    "elapsed_ms": round((time.perf_counter() - start) * 1000),
                    "steps": data.get("steps") if isinstance(data, dict) else None,
                    "trace": data.get("trace", []) if isinstance(data, dict) else [],
                    "plan_progress": ([x.get("status") for x in data.get("plan", [])]
                                      if isinstance(data, dict) else None),
                    "result": verdict, "rationale": rationale,
                    "answer": (str(data.get("answer", ""))[:1500]
                               if isinstance(data, dict) else ""),
                })
            except (httpx.HTTPError, ValueError) as exc:
                rows.append({"case": case, "result": "BLOCKED",
                             "error_type": type(exc).__name__,
                             "elapsed_ms": round((time.perf_counter()-start)*1000)})
            last_by_group[group] = rows[-1]["result"]
            print(case, rows[-1]["result"])
    results = {"started_utc": started, "source": "real Conversation API",
               "model": metadata, "rows": rows,
               "summary": {name: sum(x["result"] == name for x in rows)
                           for name in ("PASS", "FAIL", "REVIEW", "BLOCKED")},
               "limitations": [
                   "Tool traces prove invocation but cannot by themselves prove semantic answer accuracy.",
                   "Answer consistency, model request counts, user-visible browser behavior and side-effect DB inspection require independent review.",
                   "Some natural language phrasings may require repeated sampling before accepting stability.",
               ]}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", out)
    return 1 if results["summary"]["FAIL"] or results["summary"]["BLOCKED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
