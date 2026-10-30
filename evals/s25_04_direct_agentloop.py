"""S25-04 direct AgentLoop real-model acceptance with explicit trusted modes.

This isolates tool selection from M5 Host routing; not a substitute for integrated API acceptance.
Only isolated SQLite storage is used; report contains no prompts or stored values.
"""
import asyncio
import json
import os
import sqlite3
import uuid
from pathlib import Path

from agent.config import Settings
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop
from agent.mcp_stdio_client import StdioMCPClient
from agent.understanding import HTTPUnderstandingModel


class TaskStore:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks (title TEXT NOT NULL)")
    def create(self, title):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO tasks(title) VALUES (?)", (title,))
        return {"ok": True, "title": title}
    def list(self):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT title FROM tasks").fetchall()
        return {"ok": True, "tasks": [{"title": x[0]} for x in rows]}


class MemoryStore:
    def __init__(self, path, consent):
        self.path, self.consent = path, consent
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT)")
    def put(self, key, value):
        if not self.consent:
            return {"ok": False, "error": "memory_write_requires_user_consent"}
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO memory(key,value) VALUES (?,?)", (key,value))
        return {"ok": True, "key": key, "saved": True}
    def get(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
        return {"ok": True, "found": row is not None, "key": key,
                "value": row[0] if row else None}


async def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("Explicit live opt-in required")
    settings = Settings()
    if settings.model_provider == "fake":
        raise RuntimeError("Real provider required")
    folder = Path("evals/results/s25-04")
    folder.mkdir(parents=True, exist_ok=True)
    dbfile = folder / ("direct-" + uuid.uuid4().hex + ".sqlite")
    task = "S2504-" + uuid.uuid4().hex[:10]
    key, value = "key_" + uuid.uuid4().hex[:8], "value_" + uuid.uuid4().hex[:8]
    cases = []

    async def run(name, goal, mode, consent=False, mcp=None):
        loop = AgentLoop(
            NativeToolDecision(settings), task_store=TaskStore(dbfile),
            memory_store=MemoryStore(dbfile, consent),
            mcp_client=mcp or StdioMCPClient(),
            skill_strategy="semantic_topk",
            candidate_selector=HTTPUnderstandingModel(
                settings.model_provider, settings.model_base_url,
                settings.model_api_key, settings.model_timeout_seconds,
                settings.model_max_output_tokens),
            selector_model=settings.understanding_strong_model,
        )
        result = await loop.run(goal, execution_mode=mode,
                                memory_write_consent=consent)
        events = [{"name": x.get("name"), "ok": x.get("ok"),
                   "error_code": x.get("error_code")}
                  for x in result.get("trace", []) if x.get("type") == "tool"]
        case = {"name": name, "status": result["status"],
                "termination_reason": result.get("termination_reason"),
                "tool_events": events, "mode": mode,
                "topk_observed": any(x.get("type") == "topk_selection"
                                     for x in result.get("trace", []))}
        cases.append(case)
        return case, result

    try:
        w, _ = await run("authorized_task_write", "请创建一个待办，标题精确为：" + task, "execute")
        with sqlite3.connect(dbfile) as db:
            task_saved = bool(db.execute("SELECT 1 FROM tasks WHERE title=?", (task,)).fetchone())
        w["persisted"] = task_saved
        w["passed"] = task_saved and any(e["name"] == "create_task" and e["ok"] for e in w["tool_events"])

        rd, obj = await run("read_back", "请列出已创建的待办事项，包含完整标题。", "read_only")
        rd["passed"] = task in obj["answer"] and any(e["name"] == "list_tasks" and e["ok"] for e in rd["tool_events"])

        no, _ = await run("memory_no_consent", f"把键 {key} 的值保存为 {value}", "execute", False)
        with sqlite3.connect(dbfile) as db:
            not_saved = db.execute("SELECT 1 FROM memory WHERE key=?", (key,)).fetchone() is None
        no["passed"] = not_saved
        no["memory_absent"] = not_saved

        yes, _ = await run("memory_with_consent", f"我明确同意保存：键 {key}，值 {value}", "execute", True)
        with sqlite3.connect(dbfile) as db:
            saved = db.execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
        yes["passed"] = bool(saved and saved[0] == value) and any(
            e["name"] == "save_memory" and e["ok"] for e in yes["tool_events"])
        yes["memory_persisted"] = bool(saved and saved[0] == value)

        ref, result = await run("mcp_reference_source", "请在参考目录查询术语 MCP 的定义并引用来源。", "read_only")
        ref["passed"] = any(e["name"] == "mcp_reference_lookup" and e["ok"] for e in ref["tool_events"])
        ref["answer_mentions_protocol"] = "Model Context Protocol" in result.get("answer", "")

        class Outage:
            def call(self, name, arguments):
                return {"ok": False, "error": "mcp_unavailable"}
        failed, _ = await run("mcp_outage", "请在参考目录查询 MCP 的定义，若工具失败不要声称查证成功。", "read_only", mcp=Outage())
        failed["passed"] = failed["status"] != "completed" and any(
            e["name"] == "mcp_reference_lookup" and e["ok"] is False
            for e in failed["tool_events"])

        recovered, _ = await run("mcp_recovered", "MCP 参考目录恢复了，请重新查询 MCP 的定义。", "read_only")
        recovered["passed"] = any(e["name"] == "mcp_reference_lookup" and e["ok"]
                                  for e in recovered["tool_events"])
    finally:
        dbfile.unlink(missing_ok=True)

    output = {"suite": "s25-04-direct-agentloop-v1", "mode": "REAL_PROVIDER",
              "provider": settings.model_provider, "model": settings.model_name,
              "scope": "explicit trusted AgentLoop modes; excludes unified HTTP routing",
              "passed": sum(bool(x.get("passed")) for x in cases),
              "total": len(cases), "cases": cases}
    target = folder / "direct-agentloop-v1.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("Evidence:", target)
    if output["passed"] != output["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
