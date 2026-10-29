"""M4 physical readback: fake DB fixtures validate the independent read-only oracle."""
import json
from evals.s25_02_m4_physical_readback import verify
from services.db import (Base, Conversation, ConversationPlan, Message, Turn,
                         database)


def test_m4_guarded_readback_exact_identity_and_cancel_absence(tmp_path):
    path = tmp_path / "physical.db"
    engine, Session = database(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    rows = []
    cases = [
        ("CREATE", "plan", "create", "partial"),
        ("FIRST", "plan", "first", "partial"),
        ("REMAINING", "plan", "remaining", "completed"),
        ("CANCEL_PREPARE", "cancel", "prepare", "partial"),
        ("CANCEL", "cancel", "cancel", "completed"),
    ]
    with Session.begin() as db:
        ids = {}
        for group in ("plan", "cancel"):
            c = Conversation(owner_id="demo-local")
            db.add(c)
            db.flush()
            ids[group] = c.id
        for idx, (case, group, content, state) in enumerate(cases):
            user = Message(conversation_id=ids[group], role="user", content=content)
            assistant = Message(conversation_id=ids[group], role="assistant",
                                status=state, content="response")
            db.add_all([user, assistant])
            db.flush()
            t = Turn(conversation_id=ids[group], request_id=f"test-physical-{idx}",
                     state=state, assistant_message_id=assistant.id,
                     output_chars=len(assistant.content))
            db.add(t)
            db.flush()
            rows.append({"case": case, "group": group, "conversation_id": ids[group],
                         "client_request_id": t.request_id, "request": content,
                         "verdict": "PASS",
                         "response": {"turn_id": t.id, "message_id": assistant.id,
                                      "status": state}})
        snap = {"revision": 1, "steps": [
            {"status": "completed", "evidence_tool": "calculate"},
            {"status": "completed", "evidence_tool": "calculate"}]}
        db.add(ConversationPlan(conversation_id=ids["plan"],
                                snapshot=json.dumps(snap)))
    report = {"schema": "s25-m4-guarded-live-v1",
              "model": {"provider": "ollama_proxy"}, "records": rows}
    result = verify(path, report)
    assert result["summary"]["FAIL"] == 0, result["checks"]
    # Exact request identity mismatch must not be interpreted as physical PASS.
    rows[1]["client_request_id"] = "changed-request"
    bad = verify(path, report)
    assert any(item["name"] == "FIRST_exact_turn" and item["result"] == "FAIL"
               for item in bad["checks"])
    engine.dispose()
