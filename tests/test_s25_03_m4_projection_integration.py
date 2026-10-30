"""S25-03 integrated evidence is a projection of M4, never a second executor."""
import json

import pytest
from sqlalchemy import select

from services.db import (AgentAttempt, Conversation, Message, PlanToolDispatch,
                         Turn, database)
from services.migrate import migrate
from services.s25_03_projection import project_finalized_turn
from services.task_state import AgentTaskStateStore


def setup(tmp_path):
    url = f"sqlite:///{tmp_path / 'integrated.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="owner"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="t1", conversation_id="c1", request_id="request-1",
                    assistant_message_id="m1", state="running"))
    return engine, Session


def checkpoint(status="completed"):
    return {"revision": 1, "steps": [{
        "id": 1, "status": status, "expected_tool": "calculate",
        "expected_arguments": {"expression": "8*8"},
        **({"evidence_tool": "calculate"} if status == "completed" else {}),
    }]}


def dispatch(db, *, state="checkpointed", expression="8*8", receipt_ok=True):
    row = PlanToolDispatch(
        id="d1", conversation_id="c1", turn_id="t1",
        step_id=1, tool_name="calculate",
        arguments_json=json.dumps({"expression": expression}),
        status=state, receipt_json=json.dumps({"ok": receipt_ok, "value": "64"}))
    db.add(row)


def test_checkpointed_dispatch_becomes_one_bound_transition(tmp_path):
    engine, Session = setup(tmp_path)
    with Session.begin() as db:
        dispatch(db)
        db.flush()
        project_finalized_turn(db, conversation_id="c1", turn_id="t1",
                               result={"run_id": "r1"}, status="completed",
                               checkpoint=checkpoint())
        db.get(Turn, "t1").state = "completed"
    with Session() as db:
        attempt = db.get(AgentAttempt, "d1")
        assert attempt.status == "succeeded"
        assert attempt.tool_name == "calculate"
        assert attempt.arguments_digest
        assert json.loads(attempt.result_projection)["value"] == "64"
    state = AgentTaskStateStore("c1").load(Session)
    assert state["state"] == "completed"
    transitions = AgentTaskStateStore("c1").history(Session)
    assert any(t["step_id"] == 1 and t["attempt_id"] == "d1" and
               t["reason_code"] == "verified_step_completed" for t in transitions)
    engine.dispose()


def test_uncheckpointed_dispatch_is_blocked_without_success_claim(tmp_path):
    engine, Session = setup(tmp_path)
    with Session.begin() as db:
        dispatch(db, state="observed")
        db.flush()
        project_finalized_turn(db, conversation_id="c1", turn_id="t1",
                               result={"run_id": "r1"}, status="partial",
                               checkpoint=checkpoint("pending"))
    assert AgentTaskStateStore("c1").load(Session)["state"] == "blocked"
    with Session() as db:
        assert db.scalars(select(AgentAttempt)).all() == []
    engine.dispose()


@pytest.mark.parametrize("expression,receipt_ok", [
    ("9*9", True), ("8*8", False),
])
def test_invalid_checkpointed_dispatch_rolls_back_projection(
        tmp_path, expression, receipt_ok):
    engine, Session = setup(tmp_path)
    with pytest.raises(RuntimeError, match="s25_projection_unverified_execution"):
        with Session.begin() as db:
            dispatch(db, expression=expression, receipt_ok=receipt_ok)
            db.flush()
            project_finalized_turn(db, conversation_id="c1", turn_id="t1",
                                   result={"run_id": "r1"}, status="completed",
                                   checkpoint=checkpoint())
    assert AgentTaskStateStore("c1").load(Session) is None
    engine.dispose()


def test_no_tool_trace_cannot_create_execution_evidence(tmp_path):
    engine, Session = setup(tmp_path)
    with Session.begin() as db:
        project_finalized_turn(db, conversation_id="c1", turn_id="t1",
                               result={"trace": [{"type": "tool",
                                                  "name": "calculate",
                                                  "ok": True}]},
                               status="completed", checkpoint=checkpoint())
    with Session() as db:
        assert db.scalars(select(AgentAttempt)).all() == []
    transitions = AgentTaskStateStore("c1").history(Session)
    assert any(t["reason_code"] == "legacy_completion_unlinked"
               and t["attempt_id"] is None for t in transitions)
    engine.dispose()
