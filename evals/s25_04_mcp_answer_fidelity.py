"""Targeted real-provider MCP answer fidelity diagnostic.

Captures only the public mock reference entry and assistant answer.
No user memory, task contents, or unrelated conversation history is exported.
"""
import json
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.mock_mcp import MockMCPClient
from apps.api.main import create_app


def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("RUN_S25_04_LIVE=1 required")
    base = Settings()
    if base.model_provider == "fake":
        raise RuntimeError("Real provider required")
    folder = Path("evals/results/s25-04")
    folder.mkdir(parents=True, exist_ok=True)
    db = (folder / ("mcp-fidelity-" + uuid.uuid4().hex + ".sqlite")).resolve()
    settings = Settings(**{**base.model_dump(),
        "database_url": "sqlite:///" + str(db).replace("\\", "/"),
        "routing_profile": "unified", "route_admission_mode": "guarded",
        "understanding_strategy": "structured", "m5_unified_route_enabled": True,
        "m5_first_route_enabled": False, "skill_strategy": "semantic_topk",
        "skill_budget_chars": 2000})
    expected = MockMCPClient().call("mcp_reference_lookup", {"term": "MCP"})
    records = []
    try:
        with TestClient(create_app(settings)) as client:
            for i in range(1, 4):
                cid = client.post("/conversations").json()["id"]
                resp = client.post(f"/conversations/{cid}/agent/messages",
                    json={"content": "请使用 mcp_reference_lookup 查询参考词条，参数 term 精确为 MCP，并注明检索来源。",
                          "client_request_id": str(uuid.uuid4())})
                obj = resp.json()
                if not isinstance(obj, dict):
                    obj = {}
                detail = obj.get("detail") if isinstance(obj.get("detail"), dict) else {}
                trace = obj.get("trace") or []
                answer = str(obj.get("answer") or "")
                records.append({
                    "iteration": i, "http_status": resp.status_code,
                    "agent_status": obj.get("status"),
                    "reason_code": detail.get("code") or obj.get("termination_reason"),
                    "understanding_attempts": detail.get("understanding_attempts", []),
                    "tool_calls": [{"name": x.get("name"), "ok": x.get("ok"),
                                   "error_code": x.get("error_code")}
                                   for x in trace if x.get("type") == "tool"],
                    "expected_definition": expected["definition"],
                    "expected_source": expected["source"],
                    "answer": answer,
                    "exact_definition_present": expected["definition"].casefold() in answer.casefold(),
                    "source_present": expected["source"].casefold() in answer.casefold(),
                })
    finally:
        db.unlink(missing_ok=True)
    output = {"suite": "s25-04-mcp-answer-fidelity-v1",
              "head_sha_expected": os.getenv("S25_04_EXPECTED_HEAD_SHA", "not_verified"),
              "model_provider": settings.model_provider, "model": settings.model_name,
              "cases": records,
              "limitations": [
                  "Only synthetic public mock MCP reference and final assistant text recorded.",
                  "Exact definition matching is diagnostic, not a semantic correctness verdict.",
                  "No automatic subjective semantic quality pass/fail is assigned.",
              ]}
    target = folder / "mcp-answer-fidelity-v1.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": [{"iteration": x["iteration"],
        "http_status": x["http_status"], "agent_status": x["agent_status"],
        "exact_definition_present": x["exact_definition_present"],
        "source_present": x["source_present"]} for x in records]},
        ensure_ascii=False, indent=2))
    print("Evidence:", target)


if __name__ == "__main__":
    main()
