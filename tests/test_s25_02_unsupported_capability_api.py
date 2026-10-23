"""C05: unavailable tool must be denied, not needlessly clarified."""
import asyncio
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.capability_review import review_unsupported_capability
from apps.api.main import create_app


class NeverDecide:
    async def decide(self, messages, tools):
        raise AssertionError("Clarification/denial must never execute any tool")


def clarification_result():
    return json.dumps({
        "schema_version": "s25-01-v1",
        "status": "clarification_required",
        "goal": None,
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": True,
                          "reason_code": "missing_critical_constraint",
                          "question": "您是否希望我执行命令？"}}, ensure_ascii=False)


def test_c05_clear_shell_request_is_denied_without_tool_execution(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model, response_schema=None):
        if response_schema is not None:
            return json.dumps({"explicitly_requested_tool": "shell",
                               "request_is_clear": True})
        return clarification_result()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'c05.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=NeverDecide())) as client:
        cid = client.post("/conversations").json()["id"]
        res = client.post(f"/conversations/{cid}/agent/messages",
                          json={"content": "请使用系统 shell 工具执行 echo hello。",
                                "client_request_id": "c05-shell-001"})
        assert res.status_code == 200, res.text
        out = res.json()
        assert out["status"] == "partial"
        assert out["termination_reason"] == "unsupported_capability"
        assert out["trace"][0]["type"] == "unsupported_capability"
        assert out["steps"] == 0
        assert "无法" in out["answer"] and "shell" in out["answer"]


def test_b03_ambiguous_task_still_clarifies(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model, response_schema=None):
        if response_schema is not None:
            return '{"explicitly_requested_tool":null,"request_is_clear":false}'
        return clarification_result()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'c05-clarify.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=NeverDecide())) as client:
        cid = client.post("/conversations").json()["id"]
        res = client.post(f"/conversations/{cid}/agent/messages",
                          json={"content": "创建一个任务",
                                "client_request_id": "b03-clarify-001"})
        assert res.status_code == 200
        assert res.json()["trace"][0]["type"] == "goal_clarification"


class Model:
    def __init__(self, raw):
        self.raw = raw
    async def complete(self, **kwargs):
        return self.raw


def test_c05_hallucinated_tool_name_cannot_block():
    out = asyncio.run(review_unsupported_capability(
        Model('{"explicitly_requested_tool":"shell","request_is_clear":true}'),
        "strong", "请帮我创建一个任务", {"calculate"}))
    assert out is None


def test_c05_allowlisted_tool_cannot_be_denied():
    out = asyncio.run(review_unsupported_capability(
        Model('{"explicitly_requested_tool":"calculate","request_is_clear":true}'),
        "strong", "请执行 calculate", {"calculate"}))
    assert out is None


def test_f04_localized_name_of_available_tool_cannot_trigger_denial():
    class Provider:
        async def complete(self, *, messages, model, response_schema=None):
            from json import loads
            context = loads(messages[-1]["content"])
            assert "calculate" in context["available_tools"]
            assert "12/(3-3)" in context["current_message"]
            return json.dumps({
                "explicitly_requested_tool": "计算工具",
                "request_is_clear": True,
                "matches_available_capability": True,
            })

    assert asyncio.run(review_unsupported_capability(
        Provider(), "strong",
        "请调用计算工具计算 12/(3-3)，并准确报告执行情况。",
        {"calculate", "mcp_reference_lookup"})) is None


def test_f04_named_unsupported_capability_still_denied():
    assert asyncio.run(review_unsupported_capability(
        Model(json.dumps({
            "explicitly_requested_tool": "shell",
            "request_is_clear": True,
            "matches_available_capability": False,
        })),
        "strong", "请使用 shell 工具执行命令", {"calculate"})) == "shell"
