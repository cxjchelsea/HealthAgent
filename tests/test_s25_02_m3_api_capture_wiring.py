"""Guard the M3-B pre-execution read-only capture wiring."""
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


def test_capture_is_opt_in_and_diagnostic_only():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    assert "if settings.route_shadow_enabled:\n                    plan_record = db.get(ConversationPlan, cid)" in source
    assert 'response_data["route_shadow"]["m3_preturn_identity"]' in source
    assert "from services.db import Conversation, ConversationPlan, Message, Turn, database" in source
    assert "from agent.m3_turn_capture import capture_turn, compare_captured_identity" in source


def test_snapshot_identity_never_grants_execution_authority():
    from agent.m3_turn_capture import capture_turn, compare_captured_identity
    snap = capture_turn(conversation_id="c", request_id="r", turn_id="t",
                        checkpoint={"revision": 1}, pending_action=None,
                        clarification_id=None, pending_question=None)
    diagnostic = compare_captured_identity(
        snap, conversation_id="c", request_id="r", turn_id="t")
    assert diagnostic["result"] == "MATCH"
    assert diagnostic["authorization_verified"] is False
