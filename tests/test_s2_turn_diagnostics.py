"""S2 final acceptance: 503 reason is recoverable, owner-scoped and redacted."""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision, ToolDecisionError
from apps.api.main import create_app


class ProviderFailure:
    async def decide(self, messages, tools):
        raise ToolDecisionError("timeout")


class RepeatedToolCalls:
    async def decide(self, messages, tools):
        return Decision("", [{"id": "cal", "name": "calculate",
                              "arguments": {"expression": "2+2"}}])


class ExcessiveTools:
    async def decide(self, messages, tools):
        return Decision("", [{"id": str(i), "name": "calculate",
                              "arguments": {"expression": "2+2"}}
                             for i in range(5)])


def _case(tmp_path, model, expected):
    settings = Settings(database_url=f"sqlite:///{tmp_path / (expected+'.db')}",
                        user_tokens="a:alice,b:bob", allow_demo_identity=False,
                        model_provider="fake")
    with TestClient(create_app(settings, agent_decider=model)) as client:
        a = {"Authorization": "Bearer a"}
        b = {"Authorization": "Bearer b"}
        cid = client.post("/conversations", headers=a).json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", headers=a,
                               json={"content": "Please complete task",
                                     "client_request_id": "failure-test-0001"})
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == expected
        assert detail["status"] == ("provider_error" if expected == "timeout" else expected)
        assert len(detail["turn_id"]) > 10
        url = f"/conversations/{cid}/turns/{detail['turn_id']}/diagnostics"
        read = client.get(url, headers=a)
        assert read.status_code == 200
        info = read.json()
        assert info["failure_code"] == expected
        assert info["status"] == detail["status"]
        assert info["duration_ms"] >= 0
        assert isinstance(info["decision_steps"], int)
        assert client.get(url, headers=b).status_code == 404
        assert client.get(url).status_code == 401
        assert "password" not in str(detail).lower()
        assert "Please complete task" not in str(info)


def test_provider_failure_reason(tmp_path):
    _case(tmp_path, ProviderFailure(), "timeout")


def test_step_limit_reason(tmp_path):
    _case(tmp_path, RepeatedToolCalls(), "step_limit")


def test_tool_limit_reason(tmp_path):
    _case(tmp_path, ExcessiveTools(), "tool_limit")
