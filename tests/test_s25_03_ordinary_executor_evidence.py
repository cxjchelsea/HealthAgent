"""Step 4: ordinary tools use durable attempt receipts, not M4 plan dispatch."""
import asyncio
import pytest

from agent.decision import Decision
from agent.loop import AgentLoop
from services.db import (AgentAttempt, Conversation, Message, PlanToolDispatch,
                         Turn, database)
from services.evidence_store import AttemptEvidenceStore, make_attempt
from services.migrate import migrate


class Scripted:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    async def decide(self, messages, tools):
        return next(self.decisions)


def one_tool(name, arguments):
    return Decision("", [{"name": name, "arguments": arguments, "id": "tool1"}])


def setup(tmp_path):
    url = f"sqlite:///{tmp_path / 'journal.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="owner"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="t1", conversation_id="c1", request_id="r1",
                    assistant_message_id="m1", state="running"))
    return engine, Session


def test_ordinary_calculation_receipt_is_durable_without_m4_dispatch(tmp_path):
    engine, Session = setup(tmp_path)
    model = Scripted([one_tool("calculate", {"expression": "8*8"}),
                      Decision("64", [])])
    result = asyncio.run(AgentLoop(
        model, ordinary_attempt_journal=AttemptEvidenceStore("c1", "t1"),
        ordinary_attempt_session_factory=Session).run("计算8*8"))
    assert result["status"] == "completed"
    with Session() as db:
        rows = db.query(AgentAttempt).all()
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert rows[0].tool_name == "calculate"
        assert db.query(PlanToolDispatch).count() == 0
    engine.dispose()


def test_ordinary_write_blocks_when_prior_outcome_is_unknown(tmp_path):
    engine, Session = setup(tmp_path)
    journal = AttemptEvidenceStore("c1", "t1")
    journal.begin(Session, make_attempt(
        "old", "create_task", {"title": "unknown"},
        {"ok": False, "error": "prepared"}))
    model = Scripted([one_tool("create_task", {"title": "new"}),
                      Decision("不能确认之前写操作的结果", [])])
    result = asyncio.run(AgentLoop(
        model, ordinary_attempt_journal=journal,
        ordinary_attempt_session_factory=Session).run("创建一个标题为new的任务"))
    with Session() as db:
        assert db.query(AgentAttempt).count() == 1
    assert not any(t.get("ok") is True and t.get("name") == "create_task"
                   for t in result["trace"] if t.get("type") == "tool")
    engine.dispose()


def test_ordinary_executor_exception_keeps_prepared_unknown(monkeypatch, tmp_path):
    engine, Session = setup(tmp_path)
    def boom(*args, **kwargs):
        raise RuntimeError("simulated_crash_after_dispatch")
    monkeypatch.setattr("agent.loop.execute_tool", boom)
    model = Scripted([one_tool("create_task", {"title": "new"})])
    with pytest.raises(RuntimeError, match="simulated_crash"):
        asyncio.run(AgentLoop(
            model, ordinary_attempt_journal=AttemptEvidenceStore("c1", "t1"),
            ordinary_attempt_session_factory=Session).run("创建标题为new的任务"))
    with Session() as db:
        rows = db.query(AgentAttempt).all()
        assert len(rows) == 1
        assert rows[0].status == "prepared"
    engine.dispose()


@pytest.mark.parametrize("name,arguments", [
    ("create_task", {"title": "提交测试报告"}),
    ("save_memory", {"key": "qa", "value": "qa17"}),
])
def test_unified_agentloop_cannot_dispatch_unbound_writes(
        tmp_path, monkeypatch, name, arguments):
    """A model tool decision is not a Primary proposal or Host write permit."""
    engine, Session = setup(tmp_path)
    calls = []

    def prohibited(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Unbound Unified write reached execute_tool")

    monkeypatch.setattr("agent.loop.execute_tool", prohibited)
    model = Scripted([
        one_tool(name, arguments),
        Decision("无法直接执行未授权写入", []),
    ])
    try:
        result = asyncio.run(AgentLoop(
            model,
            ordinary_attempt_journal=AttemptEvidenceStore("c1", "t1"),
            ordinary_attempt_session_factory=Session,
            governed_ordinary_writes=True,
        ).run("请保存/创建相应记录", memory_write_consent=True))
        assert not calls
        assert not any(t.get("ok") and t.get("name") == name
                       for t in result["trace"] if t.get("type") == "tool")
        with Session() as db:
            assert db.query(AgentAttempt).count() == 0
    finally:
        engine.dispose()
