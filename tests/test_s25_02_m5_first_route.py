"""M5 FIRST-only adapter refuses to grant execution on its own."""
import asyncio
import json
from agent.m3_turn_capture import capture_turn
from agent.m5_first_route import propose_first_route


class Provider:
    def __init__(self, signal):
        self.signal = signal
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return json.dumps(self.signal, ensure_ascii=False)


def run(signal, message="请只执行第一步", plan=None):
    if plan is None:
        plan = {"revision": 1, "steps": [{"status": "pending"}]}
    capture = capture_turn(
        conversation_id="c", request_id="req", turn_id="t",
        checkpoint=plan, pending_action=None, clarification_id=None,
        pending_question=None)
    provider = Provider(signal)
    result = asyncio.run(propose_first_route(
        provider, "test", message, capture, conversation_id="c",
        request_id="req", turn_id="t", checkpoint=plan))
    return result, provider.calls


def test_first_proposal_binds_but_does_not_dispatch():
    message = "请只执行第一步"
    start = message.index("第一步")
    assert run({"first_step_requested": True, "quote": "第一步",
                "start": start, "end": start + 3}, message) == (True, 1)


def test_missing_or_forged_source_fails_closed():
    assert run({"first_step_requested": True, "quote": "第一步",
                "start": 0, "end": 3}) == (False, 1)
    assert run({"first_step_requested": False, "quote": "请",
                "start": 0, "end": 1}) == (False, 1)
