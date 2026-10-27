"""Control convergence: a vetoed spurious clarification cannot erase interpretation."""
import json

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


def test_f04_clarification_override_retains_read_only_host_boundaries(
    tmp_path, monkeypatch,
):
    async def complete(self, *, messages, model, response_schema=None):
        return json.dumps({
            "schema_version": "s25-01-v1",
            "status": "clarification_required",
            "goal": None,
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {
                "required": True,
                "reason_code": "missing_critical_constraint",
                "question": "是否需要进一步确认？",
            },
        }, ensure_ascii=False)

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)

    async def allowed(*args, **kwargs):
        return True

    async def available(*args, **kwargs):
        return None

    monkeypatch.setattr("apps.api.main.review_read_only_clarification", allowed)
    monkeypatch.setattr("apps.api.main.review_unsupported_capability", available)

    class FakeDecider:
        def __init__(self, settings):
            self.calls = 0

        async def decide(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return Decision("", [{"id": "calc1", "name": "calculate",
                                      "arguments": {"expression": "12/(3-3)"}}])
            return Decision("计算工具返回除零错误，未生成数值结果。", [])

    monkeypatch.setattr("apps.api.main.NativeToolDecision", FakeDecider)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path/'convergence.db'}",
        model_provider="ollama_proxy",
        understanding_strategy="structured",
    )
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请调用计算工具计算 12/(3-3)，并准确报告执行情况。",
            "client_request_id": "control-converge-001",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] in {"completed", "partial"}
        assert data.get("plan_checkpoint") is None
        assert not any(t.get("type") == "goal_clarification"
                       for t in data["trace"])
        assert any(t.get("name") == "calculate"
                   and t.get("error_code") == "division_by_zero"
                   for t in data["trace"])
