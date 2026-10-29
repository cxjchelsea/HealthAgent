"""M5 physical verification tests use synthetic fixtures, never claim live Qwen."""
import json
import pytest
from evals.s25_02_m5_first_live import first_oracle
from evals.s25_02_m5_first_physical import verify
from services.db import (Base, Conversation, ConversationPlan, Message, Turn,
                         PlanToolDispatch, database)


def test_first_oracle_requires_exact_single_tool_and_pending_second():
    payload = {"status": "partial",
               "plan": [{"status": "completed"}, {"status": "pending"}],
               "trace": [{"type": "tool", "name": "calculate", "ok": True}]}
    assert first_oracle(payload)
    assert not first_oracle(payload | {"plan": [{"status": "completed"},
                                               {"status": "completed"}]})
    assert not first_oracle(payload | {"trace": payload["trace"] * 2})


def test_physical_readback_finds_durable_dispatch_and_replay(tmp_path):
    dbpath = tmp_path / "m5.db"
    engine, Session = database(f"sqlite:///{dbpath}")
    Base.metadata.create_all(engine)
    try:
        with Session.begin() as db:
            conversation = Conversation(owner_id="test")
            db.add(conversation)
            db.flush()
            cid = conversation.id
            rows = []
            for idx, case in enumerate(("CREATE", "FIRST")):
                assistant = Message(conversation_id=cid, role="assistant",
                                    content="done", status="partial")
                db.add(assistant)
                db.flush()
                turn = Turn(conversation_id=cid, request_id=f"m5-test-{idx}",
                            state="partial", assistant_message_id=assistant.id)
                db.add(turn)
                db.flush()
                rows.append({"case": case, "client_request_id": turn.request_id,
                             "response": {"turn_id": turn.id, "message_id": assistant.id}})
            db.add(ConversationPlan(conversation_id=cid, snapshot=json.dumps(
                {"revision": 1, "steps": [
                    {"id": 1, "status": "completed", "evidence_tool": "calculate",
                     "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
                    {"id": 2, "status": "pending", "expected_tool": "calculate",
                     "expected_arguments": {"expression": "5*6"}}]})))
            db.add(PlanToolDispatch(conversation_id=cid, turn_id=turn.id,
                                    step_id=1, tool_name="calculate",
                                    arguments_json='{"expression":"3*4"}',
                                    status="checkpointed"))
        report = {"schema": "s25-m5-first-live-v1", "verdict": "PASS",
                  "model": {"provider": "ollama_proxy"}, "conversation_id": cid,
                  "records": rows + [{"case": "REPLAY", "client_request_id": rows[1]["client_request_id"],
                                     "response": {}}]}
        accepted = verify(dbpath, report)
        assert accepted["summary"]["FAIL"] == 0, accepted["checks"]
        with Session.begin() as db:
            entry = db.query(PlanToolDispatch).filter_by(conversation_id=cid).one()
            entry.status = "dispatch_unknown"
        rejected = verify(dbpath, report)
        assert any(x["name"] == "exactly_one_local_checkpointed_dispatch"
                   and x["result"] == "FAIL" for x in rejected["checks"])
        # The original oracle would accept the wrong Turn and wrong arguments.
        # Use a valid CREATE Turn id to preserve referential integrity.
        with Session.begin() as db:
            entry = db.query(PlanToolDispatch).filter_by(conversation_id=cid).one()
            entry.status = "checkpointed"
            entry.turn_id = rows[0]["response"]["turn_id"]
        wrong_turn = verify(dbpath, report)
        assert any(x["name"] == "dispatch_first_turn_and_arguments_bound"
                   and x["result"] == "FAIL" for x in wrong_turn["checks"])
        with Session.begin() as db:
            entry = db.query(PlanToolDispatch).filter_by(conversation_id=cid).one()
            entry.turn_id = rows[1]["response"]["turn_id"]
            entry.arguments_json = '{"expression":"5*6"}'
        wrong_arguments = verify(dbpath, report)
        assert any(x["name"] == "dispatch_first_turn_and_arguments_bound"
                   and x["result"] == "FAIL" for x in wrong_arguments["checks"])
        with Session.begin() as db:
            entry = db.query(PlanToolDispatch).filter_by(conversation_id=cid).one()
            entry.arguments_json = '{"expression":"3*4"}'
        assert verify(dbpath, report)["summary"]["FAIL"] == 0
    finally:
        engine.dispose()


def test_physical_rejects_incomplete_or_fake_report(tmp_path):
    with pytest.raises(ValueError, match="live_report_not_passed"):
        verify(tmp_path / "missing.db", {"schema": "s25-m5-first-live-v1",
                                        "verdict": "BLOCKED"})
