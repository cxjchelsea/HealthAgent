"""Prove actual API -> Context Builder -> LangGraph model messages are bounded."""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class RecordingDecision:
    def __init__(self):
        self.calls = []

    async def decide(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        return Decision("ack", [])


def test_actual_model_input_is_bounded_with_persisted_history(tmp_path):
    decision = RecordingDecision()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path/'context.db'}",
        model_provider="fake",
        context_max_chars=128,
        max_history_messages=3,
    )
    with TestClient(create_app(settings, agent_decider=decision)) as client:
        cid = client.post("/conversations").json()["id"]
        url = f"/conversations/{cid}/agent/messages"

        # Earlier long exchanges remain in the database but must not enter model context.
        for index in range(5):
            content = f"OLD-{index}-" + "X" * 70
            response = client.post(url, json={
                "content": content,
                "client_request_id": f"old-{index:08d}",
            })
            assert response.status_code == 200, response.text

        final = client.post(url, json={
            "content": "RECENT-QUESTION",
            "client_request_id": "recent-12345678",
        })
        assert final.status_code == 200, final.text
        assert len(decision.calls) == 6

        actual = decision.calls[-1]
        assert actual[0]["role"] == "system"
        assert actual[-1] == {"role": "user", "content": "RECENT-QUESTION"}
        # The most recent old message may fit; the earliest must be pruned.
        assert not any("OLD-0-" in m["content"] for m in actual)
        assert any("OLD-4-" in m["content"] for m in actual)
        assert actual[-2] == {"role": "assistant", "content": "ack"}
        assert sum(len(m["content"]) for m in actual[1:]) <= 128
        assert all(m["role"] in ("user", "assistant") for m in actual[1:])

        history = client.get(f"/conversations/{cid}").json()["messages"]
        assert len(history) == 12
        assert any("OLD-0-" in m["content"] for m in history)
        assert all(m["status"] == "completed" for m in history)


def test_prompt_self_identification_has_no_stage_label():
    from agent.loop import PROMPT
    assert "You are HealthAgent" in PROMPT
    assert "# HealthAgent S2-A Agent Policy" not in PROMPT
