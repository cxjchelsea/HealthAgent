"""S2-B5: deterministic end-to-end evaluation of the *production* conversation Agent route.

No model-quality claims: a scripted decision fixture is used to verify API, context,
memory/tool boundaries and persistence. Reports contain only aggregate pass/fail evidence.
"""
import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class ScenarioModel:
    def __init__(self):
        self.received = []

    async def decide(self, messages, tools):
        self.received.append([dict(x) for x in messages])
        last = messages[-1]
        if last["role"] == "tool":
            return Decision(last["content"], [])
        goal = last["content"]
        if goal.startswith("SAVE:"):
            key, value = goal[5:].split("=", 1)
            return Decision("", [{"id": "save", "name": "save_memory",
                                  "arguments": {"key": key, "value": value}}])
        if goal.startswith("READ:"):
            return Decision("", [{"id": "read", "name": "recall_memory",
                                  "arguments": {"key": goal[5:]}}])
        if goal.startswith("TASK:"):
            return Decision("", [{"id": "task", "name": "create_task",
                                  "arguments": {"title": goal[5:]}}])
        if goal == "TASKS":
            return Decision("", [{"id": "tasks", "name": "list_tasks",
                                  "arguments": {}}])
        return Decision("OK", [])


def evaluate() -> dict:
    cases = []
    started = time.monotonic()

    def check(name, fn):
        begin = time.monotonic()
        try:
            fn()
            passed, error = True, None
        except Exception as exc:
            passed, error = False, type(exc).__name__
        cases.append({"case_id": name, "passed": passed,
                      "error_type": error,
                      "latency_ms": int((time.monotonic() - begin) * 1000)})

    with tempfile.TemporaryDirectory(prefix="healthagent-s2b5-") as folder:
        url = "sqlite:///" + str(Path(folder) / "eval.db")
        settings = Settings(database_url=url, model_provider="fake",
                            user_tokens="a:user-a,b:user-b",
                            allow_demo_identity=False, max_history_messages=5,
                            context_max_chars=128)
        model = ScenarioModel()
        a, b = {"Authorization": "Bearer a"}, {"Authorization": "Bearer b"}
        with TestClient(create_app(settings, agent_decider=model)) as client:
            def conversation(headers):
                r = client.post("/conversations", headers=headers)
                assert r.status_code == 201
                return r.json()["id"]

            def send(cid, headers, goal, consent=False, request_id=None):
                return client.post(f"/conversations/{cid}/agent/messages",
                                   headers=headers,
                                   json={"content": goal,
                                         "client_request_id": request_id or str(uuid.uuid4()),
                                         "memory_write_consent": consent})

            first = conversation(a)
            second = conversation(a)
            other = conversation(b)

            def history_check():
                for idx in range(6):
                    assert send(first, a, f"HISTORY-{idx}-" + "X" * 48).status_code == 200
                previous = len(model.received)
                assert send(first, a, "RECENT").status_code == 200
                assert len(model.received) == previous + 1
                recent = model.received[-1]
                assert recent[0]["role"] == "system"
                assert recent[-1]["content"] == "RECENT"
                assert not any("HISTORY-0-" in m["content"] for m in recent)
                assert sum(len(m["content"]) for m in recent[1:]) <= 128
                assert all(m["role"] in ("user", "assistant") for m in recent[1:])
                assert len(client.get(f"/conversations/{first}", headers=a)
                           .json()["messages"]) == 14
            check("context_budget_and_history_retention", history_check)

            def denied_write():
                r = send(first, a, "SAVE:project=blue")
                assert r.status_code == 200
                assert "memory_write_requires_user_consent" in r.json()["answer"]
                assert client.get("/memories", headers=a).json() == []
            check("memory_write_denied_without_consent", denied_write)

            def consented_write():
                r = send(first, a, "SAVE:project=blue", consent=True)
                assert r.status_code == 200
                assert '"saved": true' in r.json()["answer"]
                assert client.get("/memories", headers=a).json() == [
                    {"key": "project", "value": "blue"}]
            check("memory_write_with_turn_consent", consented_write)

            def cross_conversation_recall():
                r = send(second, a, "READ:project")
                assert r.status_code == 200
                assert '"found": true' in r.json()["answer"]
                assert "blue" in r.json()["answer"]
            check("memory_cross_conversation", cross_conversation_recall)

            def user_isolation():
                r = send(other, b, "READ:project")
                assert '"found": false' in r.json()["answer"]
                assert client.get("/memories", headers=b).json() == []
                assert client.get("/memories").status_code == 401
            check("memory_owner_isolation", user_isolation)

            def denied_overwrite():
                r = send(second, a, "SAVE:project=red")
                assert "memory_write_requires_user_consent" in r.json()["answer"]
                assert "blue" in send(first, a, "READ:project").json()["answer"]
            check("memory_denied_overwrite_preserves_value", denied_overwrite)

            def correction():
                r = client.put("/memories/project", headers=a, json={"value": "green"})
                assert r.status_code == 200
                assert "green" in send(second, a, "READ:project").json()["answer"]
            check("memory_correction_visible", correction)

            def delete():
                assert client.delete("/memories/project", headers=a).status_code == 200
                r = send(first, a, "READ:project")
                assert '"found": false' in r.json()["answer"]
                assert client.get("/memories", headers=a).json() == []
            check("memory_delete_no_recall", delete)

            def task_persistence():
                assert "notebook" in send(first, a, "TASK:notebook").json()["answer"]
                assert "notebook" in send(first, a, "TASKS").json()["answer"]
                assert '"tasks": []' in send(second, a, "TASKS").json()["answer"]
            check("task_persistence_and_conversation_isolation", task_persistence)

            def duplicate_request():
                fixed = str(uuid.uuid4())
                assert send(first, a, "TASK:once", request_id=fixed).status_code == 200
                assert send(first, a, "TASK:once", request_id=fixed).status_code == 409
            check("request_id_duplicate_rejected", duplicate_request)

        with TestClient(create_app(settings, agent_decider=ScenarioModel())) as client:
            def restart_check():
                assert client.get("/memories", headers=a).json() == []
                r = client.post(f"/conversations/{first}/agent/messages", headers=a,
                                json={"content": "TASKS", "client_request_id": str(uuid.uuid4())})
                assert r.status_code == 200
                assert "notebook" in r.json()["answer"]
            check("restart_persistence_and_deleted_memory", restart_check)

    passed = sum(case["passed"] for case in cases)
    return {"suite": "s2b5-production-api-harness-v1",
            "mode": "scripted_fixture_not_real_model",
            "total": len(cases), "passed": passed,
            "pass_rate": round(passed / len(cases), 4),
            "duration_ms": int((time.monotonic()-started)*1000),
            "results": cases,
            "limitations": ["No real-model quality inference", "Character not token budget",
                            "No concurrent execution or semantic memory evaluation"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evals/results/s2b5/offline-v1.json")
    args = parser.parse_args()
    report = evaluate()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"S2-B5 scripted API harness: {report['passed']}/{report['total']} passed")
    print("evidence:", path)
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
