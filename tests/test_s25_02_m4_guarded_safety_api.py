"""M4 guarded HTTP safety acceptance over real API and deterministic injected models.

Exercise duplicate ids, concurrent active Turns and saved-plan binding refusal
without external model variability. This is not live Qwen or PostgreSQL evidence.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class BlockingDecider:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        self.entered.set()
        await asyncio.to_thread(self.release.wait, 15)
        return Decision(content="complete", tool_calls=[])


def test_guarded_same_turn_replay_and_cross_request_concurrency(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm4_guarded_concurrent.db'}",
        model_provider="fake", route_admission_mode="guarded")
    decider = BlockingDecider()
    with TestClient(create_app(settings, agent_decider=decider)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        first = {"content": "Please answer directly",
                 "client_request_id": "m4-concurrent-request-001"}
        competing = {"content": "Another independent question",
                     "client_request_id": "m4-concurrent-request-002"}
        with ThreadPoolExecutor(max_workers=2) as workers:
            future = workers.submit(client.post, endpoint, json=first)
            try:
                assert decider.entered.wait(timeout=5)
                blocked = client.post(endpoint, json=competing)
                assert blocked.status_code == 409
                assert blocked.json()["detail"] == "conversation_turn_in_progress"
            finally:
                decider.release.set()
            assert future.result(timeout=15).status_code == 200
        replay = client.post(endpoint, json=first)
        assert replay.status_code == 409
        assert replay.json()["detail"] == "duplicate_request_id"
        assert decider.calls == 1
        assert client.post(endpoint, json=competing).status_code == 200
        assert decider.calls == 2


def test_guarded_execution_fails_closed_for_stale_or_completed_snapshot():
    from agent.m4_guarded_execution import admit_guarded_plan_execution
    steps = [
        {"id": 1, "status": "completed", "goal": "计算 3*4",
         "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
        {"id": 2, "status": "pending", "goal": "计算 5*6",
         "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}},
    ]
    base = dict(conversation_id="cid", request_id="m4-stale-001",
                checkpoint={"revision": 2, "steps": steps},
                scope="execute_first", turn_created=True,
                semantic_confirmed=True)
    assert not admit_guarded_plan_execution(**base)
    assert not admit_guarded_plan_execution(**(base | {"checkpoint": {
        "revision": 0, "steps": steps}}))
    assert not admit_guarded_plan_execution(**(base | {"checkpoint": {
        "revision": 2, "steps": [{**steps[0], "status": "unknown"}, steps[1]]}}))
