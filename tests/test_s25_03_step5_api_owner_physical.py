"""Step 5 physical-ish API acceptance: real SQLite, owner fence, durable readback."""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app
from services.db import AgentAttempt, database


class Calculator:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "calc1", "name": "calculate",
                                  "arguments": {"expression": "7*6"}}])
        return Decision("The verified result is 42.", [])


def test_owner_scoped_evidence_is_durable_and_no_duplicate_on_read(tmp_path):
    url = f"sqlite:///{tmp_path / 'step5.db'}"
    cfg = Settings(database_url=url, model_provider="fake",
                   user_tokens="alpha:alice,beta:bob", allow_demo_identity=False)
    with TestClient(create_app(cfg, agent_decider=Calculator())) as client:
        owner = {"Authorization": "Bearer alpha"}
        outsider = {"Authorization": "Bearer beta"}
        created = client.post("/conversations", headers=owner)
        assert created.status_code == 200
        cid = created.json()["id"]
        r = client.post(f"/conversations/{cid}/agent/messages", headers=owner,
                        json={"content": "Compute 7*6",
                              "client_request_id": "s25-03-step5-api-001"})
        assert r.status_code == 200, r.text
        turn_id = r.json()["turn_id"]
        evidence_path = f"/conversations/{cid}/turns/{turn_id}/evidence"
        history_path = f"/conversations/{cid}/agent/task-history"
        state_path = f"/conversations/{cid}/agent/task-state"
        evidence = client.get(evidence_path, headers=owner)
        assert evidence.status_code == 200, evidence.text
        attempts = evidence.json()["attempts"]
        assert len(attempts) == 1
        assert attempts[0]["tool"] == "calculate"
        assert attempts[0]["status"] == "succeeded"
        assert attempts[0]["result"]["value"] == 42
        assert client.get(evidence_path, headers=owner).json() == evidence.json()
        for path in (evidence_path, history_path, state_path):
            # Unauthorized and wrong-owner reads must never leak records.
            assert client.get(path).status_code == 401
            assert client.get(path, headers=outsider).status_code == 404
        assert client.get(history_path, headers=owner).status_code == 200
        assert client.get(state_path, headers=owner).status_code == 200
    _, Session = database(url)
    with Session() as db:
        durable = db.query(AgentAttempt).filter(
            AgentAttempt.turn_id == turn_id).all()
        assert len(durable) == 1
        assert durable[0].status == "succeeded"
