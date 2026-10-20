"""Physical conversation API checkpoint continuation, correction and owner tests."""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class MultiTurnPlanner:
    def __init__(self):
        self.i = 0

    async def decide(self, messages, tools):
        self.i += 1
        calls = {
            1: ("propose_plan", {"steps": [
                {"goal": "calculate 8*8", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "8*8"}},
                {"goal": "calculate 9*9", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "9*9"}}]}),
            2: ("calculate", {"expression": "8*8"}),
            4: ("revise_plan", {"reason": "user changed pending calculation",
                               "pending_steps": [{"goal": "calculate 10*10",
                                  "expected_tool": "calculate",
                                  "expected_arguments": {"expression": "10*10"}}]}),
            5: ("calculate", {"expression": "10*10"}),
            7: ("revise_plan", {"reason": "user corrected completed second step",
                               "invalidate_step_ids": [3],
                               "pending_steps": [{"goal": "calculate 11*11",
                                  "expected_tool": "calculate",
                                  "expected_arguments": {"expression": "11*11"}}]}),
            8: ("calculate", {"expression": "11*11"}),
        }
        if self.i not in calls:
            return Decision("这是当前结果。", [])
        name, args = calls[self.i]
        return Decision("", [{"id": "call-" + str(self.i), "name": name,
                              "arguments": args}])


def test_api_multiturn_pending_and_completed_correction(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path/'multi.db'}",
                        user_tokens="a:alice,b:bob", allow_demo_identity=False)
    headers = {"Authorization": "Bearer a"}
    with TestClient(create_app(settings, agent_decider=MultiTurnPlanner())) as client:
        cid = client.post("/conversations", headers=headers).json()["id"]
        route = f"/conversations/{cid}/agent/messages"

        def send(text, ident):
            reply = client.post(route, headers=headers, json={
                "content": text, "client_request_id": ident})
            assert reply.status_code == 200, reply.text
            return reply.json()

        first = send("请制定计划，计算 8*8 和 9*9", "s2d-multi-001")
        assert first["status"] == "partial"
        assert first["plan"][0]["status"] == "completed"
        assert first["plan"][1]["status"] == "pending"

        denied = client.post(route, headers={"Authorization": "Bearer b"}, json={
            "content": "继续计划", "client_request_id": "s2d-multi-deny"})
        assert denied.status_code == 404

        second = send("更正第二步，改算 10*10", "s2d-multi-002")
        assert second["status"] == "completed"
        assert second["plan_revision"] == 2
        assert second["plan"][0]["status"] == "completed"
        assert second["plan"][1]["expected_arguments"]["expression"] == "10*10"
        assert [step["id"] for step in second["plan"]] == [1, 3]

        third = send("再次更正已经完成的第二步，改算 11*11", "s2d-multi-003")
        assert third["status"] == "completed"
        assert third["plan_revision"] == 3
        assert third["plan"][0]["expected_arguments"]["expression"] == "8*8"
        assert third["plan"][1]["expected_arguments"]["expression"] == "11*11"
        assert [step["id"] for step in third["plan"]] == [1, 4]
        assert third["plan_events"][-2]["invalidated"] == [3]
