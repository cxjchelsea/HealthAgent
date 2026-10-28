"""Guard the M3-B pre-execution capture and its diagnostic-only wiring."""
from pathlib import Path


def test_capture_is_inside_turn_creation_transaction_before_legacy_execution():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    tx_start = source.index("with Session.begin() as db:", source.index(
        'async def send_agent_in_conversation'))
    turn_flush = source.index("assistant_id, turn_id = response_message.id, turn.id", tx_start)
    capture = source.index("preturn_capture = capture_turn(", turn_flush)
    tx_end = source.index("except IntegrityError as exc:", capture)
    legacy_store = source.index("existing_checkpoint = plan_store.load()", tx_end)
    first_legacy_control = source.index("if body.goal_confirmation is not None:", legacy_store)
    assert tx_start < turn_flush < capture < tx_end < legacy_store < first_legacy_control


def test_capture_is_opt_in_and_checkpoint_comparison_is_diagnostic_only():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    assert "plan_record = db.get(ConversationPlan, cid)" in source
    assert "if settings.route_shadow_enabled:\n                    preturn_capture = capture_turn(" in source
    assert 'response_data["route_shadow"]["m3_preturn_identity"]' in source
    assert 'response_data["route_shadow"]["m3_checkpoint_snapshot"]' in source
    assert "compare_checkpoint_snapshot(preturn_capture, existing_checkpoint)" in source
    assert "from services.db import Conversation, ConversationPlan, Message, Turn, database" in source
    assert "from agent.m3_turn_capture import (" in source
    assert "capture_turn, compare_captured_identity, compare_checkpoint_snapshot," in source


def test_snapshot_identity_never_grants_execution_authority():
    from agent.m3_turn_capture import capture_turn, compare_captured_identity
    snap = capture_turn(conversation_id="c", request_id="r", turn_id="t",
                        checkpoint={"revision": 1}, pending_action=None,
                        clarification_id=None, pending_question=None)
    diagnostic = compare_captured_identity(
        snap, conversation_id="c", request_id="r", turn_id="t")
    assert diagnostic["result"] == "MATCH"
    assert diagnostic["authorization_verified"] is False


def test_sqlite_plan_change_between_capture_and_legacy_read_is_detected():
    import json

    from sqlalchemy import select
    from services.db import Base, Conversation, ConversationPlan, Turn, database
    from agent.m3_turn_capture import capture_turn, compare_checkpoint_snapshot

    engine, Session = database("sqlite://")
    try:
        Base.metadata.create_all(engine)
        with Session.begin() as db:
            conversation = Conversation(owner_id="m3-test")
            db.add(conversation)
            db.flush()
            cid = conversation.id
            db.add(ConversationPlan(conversation_id=cid,
                                    snapshot=json.dumps({"revision": 1, "steps": []})))

        with Session.begin() as db:
            plan = db.get(ConversationPlan, cid)
            preturn_checkpoint = json.loads(plan.snapshot)
            turn = Turn(conversation_id=cid, request_id="request-m3-test")
            db.add(turn)
            db.flush()
            captured = capture_turn(
                conversation_id=cid, request_id=turn.request_id, turn_id=turn.id,
                checkpoint=preturn_checkpoint, pending_action=None,
                clarification_id=None, pending_question=None)

        with Session.begin() as db:
            plan = db.get(ConversationPlan, cid)
            plan.snapshot = json.dumps({"revision": 2, "steps": []})

        with Session() as db:
            plan = db.scalar(select(ConversationPlan).where(
                ConversationPlan.conversation_id == cid))
            actual_checkpoint = json.loads(plan.snapshot)

        comparison = compare_checkpoint_snapshot(captured, actual_checkpoint)
        assert comparison["result"] == "CHANGED"
        assert comparison["captured_revision"] == 1
        assert comparison["actual_revision"] == 2
        assert comparison["authorization_verified"] is False
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
