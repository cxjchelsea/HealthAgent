"""Independent model shortlist cannot widen Host authority and safely falls back."""
import asyncio
import json

from agent.decision import Decision
from agent.loop import AgentLoop
from agent.semantic_topk import select_read_tools
from agent.tools import TOOL_SCHEMAS


class Provider:
    def __init__(self, payload=None, fail=False):
        self.payload = payload
        self.fail = fail

    async def complete(self, **kwargs):
        if self.fail:
            raise RuntimeError("provider down")
        return json.dumps(self.payload)


def allowed():
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in {
        "calculate", "utc_time", "list_tasks"}]


def test_shortlist_and_invalid_fallback():
    tools = allowed()
    expected = {"calculate", "utc_time", "list_tasks"}
    assert asyncio.run(select_read_tools(
        Provider({"tools": ["calculate"], "uncertain": False}),
        "test", "How much is seven of ten", tools)) == ({"calculate"}, "selected")
    assert asyncio.run(select_read_tools(
        Provider({"tools": ["save_memory"], "uncertain": False}),
        "test", "Please save", tools))[0] == expected
    assert asyncio.run(select_read_tools(
        Provider({"tools": [], "uncertain": True}),
        "test", "Ambiguous", tools))[0] == expected
    assert asyncio.run(select_read_tools(
        Provider(fail=True), "test", "anything", tools))[0] == expected


class Probe:
    def __init__(self):
        self.seen = []

    async def decide(self, messages, tools):
        self.seen.append({t["function"]["name"] for t in tools})
        return Decision("Done", [])


def test_host_readonly_subset_and_respond_only():
    probe = Probe()
    asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["calculate"], "uncertain": False}),
        selector_model="test").run("Please verify 7/10", execution_mode="read_only"))
    assert probe.seen == [{"request_clarification", "calculate"}]
    probe = Probe()
    asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["calculate"], "uncertain": False}),
        selector_model="test").run("Explain the idea", execution_mode="respond_only"))
    assert probe.seen == [set()]


def test_topk_never_admits_untrusted_write():
    probe = Probe()
    asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["create_task"], "uncertain": False}),
        selector_model="test").run("Create a task", execution_mode="read_only"))
    assert "create_task" not in probe.seen[0]
    assert "save_memory" not in probe.seen[0]


def test_topk_observability_exposes_only_names_and_bounded_metrics():
    probe = Probe()
    result = asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["calculate"], "uncertain": False}),
        selector_model="test").run("Verify seven of ten", execution_mode="read_only"))
    selected = [event for event in result["trace"] if event["type"] == "topk_selection"]
    assert len(selected) == 1
    trace = selected[0]
    assert trace["reason"] == "selected"
    assert trace["selected_read_tools"] == ["calculate"]
    assert trace["candidate_count"] >= 1
    assert trace["selector_duration_ms"] >= 0
    assert trace["selector_model_calls"] == 1
    assert set(trace["exposed_tools"]) == {"request_clarification", "calculate"}
    assert "Verify seven of ten" not in str(trace)


def test_topk_fallback_reports_reason_and_never_loses_read_candidates():
    probe = Probe()
    result = asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider(fail=True), selector_model="test").run(
            "Need a checked result", execution_mode="read_only"))
    trace = next(event for event in result["trace"]
                 if event["type"] == "topk_selection")
    assert trace["reason"] == "fallback_provider_error"
    assert "calculate" in trace["exposed_tools"]
    assert "create_task" not in trace["exposed_tools"]


def test_final_read_only_fence_and_uncertain_fallback():
    probe = Probe()
    result = asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": [], "uncertain": True}),
        selector_model="test").run("Review the current tasks", execution_mode="read_only"))
    assert "list_tasks" in probe.seen[0]
    assert "create_task" not in probe.seen[0]
    assert "save_memory" not in probe.seen[0]
    assert any(e.get("type") == "topk_selection" and
               e.get("reason") == "fallback_invalid_or_uncertain"
               for e in result["trace"])


class ToolAttempt:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "probe1", "name": self.name,
                                  "arguments": self.arguments}])
        return Decision("Reviewed the observation", [])


def test_readonly_rejects_model_requested_write_at_executor_boundary():
    decider = ToolAttempt("create_task", {"title": "must never be created"})
    result = asyncio.run(AgentLoop(
        decider, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["list_tasks"], "uncertain": False}),
        selector_model="test").run("Check current tasks", execution_mode="read_only"))
    rejected = [e for e in result["trace"]
                if e.get("type") == "tool" and e.get("name") == "create_task"]
    assert rejected and all(e.get("ok") is False for e in rejected)


def test_respond_only_rejects_model_requested_read_tool():
    decider = ToolAttempt("calculate", {"expression": "5/7*100"})
    result = asyncio.run(AgentLoop(
        decider, skill_strategy="semantic_topk",
        candidate_selector=Provider(fail=True), selector_model="test"
    ).run("Explain goals", execution_mode="respond_only"))
    rejected = [e for e in result["trace"]
                if e.get("type") == "tool" and e.get("name") == "calculate"]
    assert rejected and all(e.get("ok") is False for e in rejected)
    assert not any(e.get("type") == "topk_selection" for e in result["trace"])


class InMemoryTasks:
    def __init__(self):
        self.items = []
    def create(self, title):
        self.items.append(title)
        return {"ok": True, "title": title, "task_id": str(len(self.items))}
    def list(self):
        return {"ok": True, "tasks": [
            {"title": title, "task_id": str(i + 1)}
            for i, title in enumerate(self.items)]}


def test_authorized_execute_task_remains_persistent_across_turns():
    store = InMemoryTasks()
    decider = ToolAttempt("create_task", {"title": "S25-04 approved test task"})
    result = asyncio.run(AgentLoop(
        decider, task_store=store, skill_strategy="dynamic"
    ).run("Create a task: S25-04 approved test task", execution_mode="execute"))
    assert store.items == ["S25-04 approved test task"]
    assert any(e.get("name") == "create_task" and e.get("ok") is True
               for e in result["trace"] if e.get("type") == "tool")
    assert store.list()["tasks"][0]["title"] == "S25-04 approved test task"


def test_unapproved_read_only_write_leaves_task_store_unchanged():
    store = InMemoryTasks()
    decider = ToolAttempt("create_task", {"title": "unapproved test task"})
    result = asyncio.run(AgentLoop(
        decider, task_store=store, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["list_tasks"], "uncertain": False}),
        selector_model="test").run("Look up tasks", execution_mode="read_only"))
    assert store.items == []
    assert not any(e.get("name") == "create_task" and e.get("ok") is True
                   for e in result["trace"] if e.get("type") == "tool")


def test_sqlite_task_survives_store_and_engine_recreation(tmp_path):
    """True file-backed persistence, independent of model tool exposure."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from services.db import Base, Conversation, ConversationTask
    from services.task_store import ConversationTaskStore

    filename = tmp_path / "s25-04-durable-task.sqlite"
    url = "sqlite:///" + str(filename)
    engine1 = create_engine(url)
    Base.metadata.create_all(engine1)
    Session1 = sessionmaker(bind=engine1)
    with Session1.begin() as db:
        db.add(Conversation(id="s25-04-test-conv", owner_id="test-owner"))
    first = ConversationTaskStore(Session1, "s25-04-test-conv")
    result = first.create("S25-04 persisted task")
    assert result["ok"] is True
    engine1.dispose()

    engine2 = create_engine(url)
    Session2 = sessionmaker(bind=engine2)
    second = ConversationTaskStore(Session2, "s25-04-test-conv")
    stored = second.list()
    assert stored["ok"] is True
    assert [(x["task_id"], x["title"]) for x in stored["tasks"]] == [
        (result["task_id"], "S25-04 persisted task")]
    engine2.dispose()
