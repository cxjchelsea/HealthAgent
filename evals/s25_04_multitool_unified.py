"""S25-04 Work Package B: real Unified HTTP multi-tool observation gate."""
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.mcp_stdio_client import StdioMCPClient
from apps.api.main import create_app


def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("RUN_S25_04_LIVE=1 required")
    base = Settings()
    if base.model_provider == "fake":
        raise RuntimeError("real provider required")
    folder = Path("evals/results/s25-04")
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / ("multitool-" + uuid.uuid4().hex + ".sqlite")
    url = "sqlite:///" + str(database.resolve()).replace("\\", "/")
    settings = Settings(**{**base.model_dump(), "routing_profile": "unified",
        "route_admission_mode": "guarded", "understanding_strategy": "structured",
        "m5_unified_route_enabled": True, "m5_first_route_enabled": False,
        "skill_strategy": "semantic_topk", "skill_budget_chars": 2000,
        "database_url": url})
    repeats = int(os.getenv("S25_04_MULTITOOL_REPEATS", "5"))
    if not 1 <= repeats <= 10:
        raise ValueError("S25_04_MULTITOOL_REPEATS must be 1..10")
    checks = []

    def request(client, cid, prompt):
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": prompt, "client_request_id": str(uuid.uuid4())})
        data = response.json()
        trace = data.get("trace") or []
        return response.status_code, data, trace

    def record(name, accepted, status, data, trace, **extra):
        events = [{"tool": e.get("name"), "ok": e.get("ok"),
                   "error_code": e.get("error_code")} for e in trace
                  if e.get("type") == "tool"]
        exposures = [e.get("tool_names", []) for e in trace
                     if e.get("type") == "tool_exposure"]
        decisions = [e.get("step") for e in trace if e.get("type") == "decision"]
        tool_steps = [e.get("step") for e in trace if e.get("type") == "tool"]
        checks.append({"case": name, "passed": bool(accepted),
            "decision_steps": decisions, "tool_steps": tool_steps,
            "http_status": status, "agent_status": data.get("status"),
            "termination_reason": data.get("termination_reason"),
            "tools": events, "exposures": exposures, **extra})

    try:
        with TestClient(create_app(settings)) as client:
          for repeat in range(1, repeats + 1):
            cid = client.post("/conversations").json()["id"]
            code, data, trace = request(client, cid,
                "请先从 MCP 参考目录查询 MCP 的定义，再用计算工具计算 (19+23)*4，给出检索来源和计算结果。")
            tools = [(e.get("name"), e.get("ok")) for e in trace
                     if e.get("type") == "tool"]
            names = [name for name, ok in tools if ok]
            sequential_steps = [e.get("step") for e in trace
                if e.get("type") == "tool" and e.get("name") in
                {"mcp_reference_lookup", "calculate"} and e.get("ok")]
            distinct_decisions = (len(sequential_steps) >= 2 and
                                  all(isinstance(x, int) for x in sequential_steps[:2])
                                  and sequential_steps[0] < sequential_steps[1])
            passed = (distinct_decisions and code == 200 and data.get("status") == "completed"
                      and "mcp_reference_lookup" in names
                      and "calculate" in names
                      and names.index("mcp_reference_lookup") <
                          names.index("calculate")
                      and "168" in data.get("answer", ""))
            record("sequential_lookup_then_calculate", passed, code, data, trace,
                   repeat=repeat, distinct_decisions=distinct_decisions,
                   lookup_before_calculation=("mcp_reference_lookup" in names
                   and "calculate" in names and names.index("mcp_reference_lookup")
                   < names.index("calculate")))

            def unavailable(self, name, arguments):
                if name == "mcp_reference_lookup":
                    return {"ok": False, "error": "mcp_unavailable"}
                return original(self, name, arguments)

            original = StdioMCPClient.call
            failcid = client.post("/conversations").json()["id"]
            with patch.object(StdioMCPClient, "call", unavailable):
                code, data, trace = request(client, failcid,
                    "先从 MCP 参考目录核实 MCP 的定义；如果查询失败，就明确告知未核实，不要声称查询成功。")
            failure = any(e.get("type") == "tool"
                and e.get("name") == "mcp_reference_lookup"
                and e.get("ok") is False for e in trace)
            false_success = any(e.get("type") == "tool"
                and e.get("name") == "mcp_reference_lookup"
                and e.get("ok") is True for e in trace)
            exposures = [e.get("tool_names", []) for e in trace
                         if e.get("type") == "tool_exposure"]
            excluded_on_followup = bool(exposures) and any(
                "mcp_reference_lookup" not in x for x in exposures[1:])
            mcp_attempts = sum(e.get("type") == "tool" and
                e.get("name") == "mcp_reference_lookup" for e in trace)
            record("mcp_failure_observation_reselection",
                failure and not false_success and mcp_attempts == 1
                and excluded_on_followup and data.get("status") != "completed",
                code, data, trace,
                repeat=repeat, mcp_attempts=mcp_attempts,
                failure_observed=failure,
                candidate_excluded_on_followup=excluded_on_followup)
    finally:
        database.unlink(missing_ok=True)

    result = {"suite": "s25-04-multitool-unified-v2", "repeats": repeats,
        "profile": settings.effective_routing_profile,
        "model": settings.understanding_strong_model,
        "passed": sum(x["passed"] for x in checks),
        "total": len(checks), "cases": checks,
        "notes": ["Repeated tests are independent conversations in a shared isolated test database.",
                  "MCP outage is controlled; real model generates decisions.",
                  "Tool-event ordering and exposure lists are required evidence."]}
    target = folder / "multitool-unified-v2.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
