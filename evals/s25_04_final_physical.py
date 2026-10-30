"""Opt-in final S25-04 Qwen physical acceptance; isolated SQLite, no real user data."""
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from agent.config import Settings
from agent.mcp_stdio_client import StdioMCPClient
from agent.mock_mcp import MockMCPClient
from apps.api.main import create_app
from services.db import ConversationTask, UserMemory


def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("Set RUN_S25_04_LIVE=1 before making real provider requests")
    base = Settings()
    if base.model_provider == "fake":
        raise RuntimeError("Real provider required")
    folder = Path("evals/results/s25-04")
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / ("final-physical-" + uuid.uuid4().hex + ".sqlite")
    url = "sqlite:///" + str(database.resolve()).replace("\\", "/")
    # Isolate S25-04 tool policy from the separate M5 unified saved-plan gate.
    # A rejected M5 plan is not evidence that the Top-K AgentLoop executed.
    settings = Settings(**{**base.model_dump(), "skill_strategy": "semantic_topk",
                           "routing_profile": "legacy",
                           "route_admission_mode": "legacy",
                           "understanding_strategy": "direct",
                           "m5_unified_route_enabled": False,
                           "m5_first_route_enabled": False,
                           "database_url": url, "skill_budget_chars": 2000})
    engine = create_engine(url)
    records = []

    def check(name, ok, info):
        records.append({"scenario": name, "passed": bool(ok), **info})

    def send(client, cid, prompt, consent=False):
        reply = client.post(f"/conversations/{cid}/agent/messages",
                            json={"content": prompt, "client_request_id": str(uuid.uuid4()),
                                  "memory_write_consent": consent})
        obj = reply.json()
        trace = obj.get("trace") or []
        detail = obj.get("detail") if isinstance(obj.get("detail"), dict) else {}
        error_code = detail.get("code") or obj.get("failure_code") or obj.get("termination_reason")
        if not error_code and reply.status_code >= 400:
            error_code = "unclassified_http_error"
        return {"status": reply.status_code, "agent_status": obj.get("status"),
                "execution_mode": obj.get("execution_mode"),
                "trace_types": [e.get("type") for e in trace],
                "reason_code": error_code,
                "termination_reason": obj.get("termination_reason"),
                "tools": [{"name": e.get("name"), "ok": e.get("ok"),
                           "error_code": e.get("error_code")}
                          for e in trace if e.get("type") == "tool"],
                "answer": obj.get("answer", "")}

    def diagnostic(row):
        return {"http_status": row["status"], "reason_code": row["reason_code"],
                "execution_mode": row["execution_mode"],
                "agent_status": row["agent_status"],
                "termination_reason": row["termination_reason"],
                "trace_types": row["trace_types"], "tool_events": row["tools"]}

    def tasks(cid):
        with Session(engine) as db:
            return [x.title for x in db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()]

    def memory():
        with Session(engine) as db:
            return {x.key: x.value for x in db.scalars(select(UserMemory)).all()}

    task_title = "S2504-phys-" + uuid.uuid4().hex[:10]
    key = "s2504_phys_" + uuid.uuid4().hex[:10]
    value = "proof_" + uuid.uuid4().hex[:10]
    try:
        with TestClient(create_app(settings)) as client:
            cid = client.post("/conversations").json()["id"]
            write = send(client, cid, "请创建一个待办任务，标题精确为：" + task_title)
            task_saved = task_title in tasks(cid)
            check("authorized_task_write", task_saved and any(
                t["name"] == "create_task" and t["ok"] for t in write["tools"]),
                {"http_status": write["status"], "agent_status": write["agent_status"],
                 "tool_events": write["tools"], "persisted": task_saved,
                 "execution_mode": write["execution_mode"], "trace_types": write["trace_types"],
                 "termination_reason": write["termination_reason"],
                 "reason_code": write["reason_code"]})
            read = send(client, cid, "请查询这个会话的待办并告诉我任务标题。")
            check("same_conversation_read", task_title in read["answer"] and any(
                t["name"] == "list_tasks" and t["ok"] for t in read["tools"]),
                {"http_status": read["status"], "tool_events": read["tools"],
                 "reason_code": read["reason_code"], "trace_types": read["trace_types"],
                 "answer_contains_task": task_title in read["answer"]})
            denied = send(client, cid,
                          f"请把长期记忆键 {key} 的值保存成 {value}，并尝试确认是否保存。",
                          consent=False)
            check("memory_no_consent", key not in memory(),
                  {"http_status": denied["status"], "reason_code": denied["reason_code"],
                   "tool_events": denied["tools"],
                   "memory_absent": key not in memory(),
                   "execution_mode": denied["execution_mode"],
                   "trace_types": denied["trace_types"]})
            allowed = send(client, cid,
                           f"我明确同意本轮保存长期记忆，请把键 {key} 的值保存成 {value}。",
                           consent=True)
            check("memory_with_consent", memory().get(key) == value and any(
                t["name"] == "save_memory" and t["ok"] for t in allowed["tools"]),
                {"http_status": allowed["status"], "tool_events": allowed["tools"],
                 "memory_persisted": memory().get(key) == value,
                 "execution_mode": allowed["execution_mode"],
                 "trace_types": allowed["trace_types"],
                 "termination_reason": allowed["termination_reason"],
                 "reason_code": allowed["reason_code"]})

            expected = MockMCPClient().call("mcp_reference_lookup", {"term": "MCP"})
            reference = send(client, cid, "请查询 MCP 参考目录中 MCP 的定义，注明检索来源。")
            definition = str(expected.get("definition", ""))
            check("mcp_source_answer", bool(expected.get("ok")) and bool(definition)
                  and definition.casefold() in reference["answer"].casefold()
                  and any(t["name"] == "mcp_reference_lookup" and t["ok"]
                          for t in reference["tools"]),
                  {"http_status": reference["status"], "tool_events": reference["tools"],
                   "trace_types": reference["trace_types"],
                   "execution_mode": reference["execution_mode"],
                   "definition_matched": bool(definition) and
                   definition.casefold() in reference["answer"].casefold()})

            original_call = StdioMCPClient.call
            def unavailable(self, name, arguments):
                if name == "mcp_reference_lookup":
                    return {"ok": False, "error": "mcp_unavailable"}
                return original_call(self, name, arguments)
            with patch.object(StdioMCPClient, "call", unavailable):
                failure = send(client, cid, "请调用 MCP 参考目录核实 MCP 定义，未查证不能说已验证。")
            failed_tool = any(t["name"] == "mcp_reference_lookup" and
                              t["ok"] is False for t in failure["tools"])
            check("mcp_outage_no_success", failed_tool and
                  not any(t["name"] == "mcp_reference_lookup" and t["ok"]
                          for t in failure["tools"]) and failure["agent_status"] != "completed",
                  {"http_status": failure["status"], "agent_status": failure["agent_status"],
                   "termination_reason": failure["termination_reason"],
                   "tool_events": failure["tools"]})

        # A new application and the same persistent conversation/database.
        with TestClient(create_app(settings)) as restarted:
            restored = restarted.get(f"/conversations/{cid}")
            recovered = send(restarted, cid, "MCP 参考服务已恢复，请重新调用工具核实 MCP 定义。")
            check("restarted_conversation_mcp_recovery",
                  restored.status_code == 200 and task_title in tasks(cid)
                  and any(t["name"] == "mcp_reference_lookup" and t["ok"]
                          for t in recovered["tools"]),
                  {"history_http_status": restored.status_code,
                   "http_status": recovered["status"], "agent_status": recovered["agent_status"],
                   "tool_events": recovered["tools"], "task_persisted": task_title in tasks(cid),
                   "trace_types": recovered["trace_types"],
                   "execution_mode": recovered["execution_mode"],
                   "reason_code": recovered["reason_code"]})
    finally:
        engine.dispose()
        database.unlink(missing_ok=True)

    report = {"suite": "s25-04-final-physical-v2", "mode": "REAL_PROVIDER",
              "model": settings.model_name, "provider": settings.model_provider,
              "routing_profile": settings.effective_routing_profile,
              "passed": sum(r["passed"] for r in records), "total": len(records),
              "cases": records,
              "limitations": ["Model-dependent behavior: failures require review, not forced success.",
                              "MCP outage is a controlled transport fault; provider remains real.",
                              "Legacy routing isolates Top-K; separate M5 unified authorization is not evaluated.",
                              "Report omits prompts, final answer text and memory values.",
                              "Isolated SQLite; does not simulate a force-killed process."]}
    target = folder / "final-physical-v2.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"],
                      "cases": [{"scenario": x["scenario"], "passed": x["passed"]}
                                for x in records]}, ensure_ascii=False, indent=2))
    print("Evidence:", target)
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
