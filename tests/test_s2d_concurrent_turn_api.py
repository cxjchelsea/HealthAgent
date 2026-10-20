"""Concurrent requests at the real conversation HTTP entry point."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class BlockingDecision:
    def __init__(self):
        self.entered = Event()
        self.release = Event()

    async def decide(self, messages, tools):
        self.entered.set()
        await asyncio.to_thread(self.release.wait, 15)
        return Decision(content="finished", tool_calls=[])


def test_same_conversation_conflict_and_release(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'concurrent.db'}",
                        model_provider="fake")
    decider = BlockingDecision()
    with TestClient(create_app(settings, agent_decider=decider)) as client:
        cid = client.post("/conversations").json()["id"]
        url = f"/conversations/{cid}/agent/messages"
        first = {"content": "First task", "client_request_id": "request-first-001"}
        second = {"content": "Second task", "client_request_id": "request-second-002"}
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(client.post, url, json=first)
            try:
                assert decider.entered.wait(timeout=5)
                conflict = client.post(url, json=second)
                assert conflict.status_code == 409
                assert conflict.json()["detail"] == "conversation_turn_in_progress"
            finally:
                decider.release.set()
            assert pending.result(timeout=10).status_code == 200
        assert client.post(url, json=second).status_code == 200
