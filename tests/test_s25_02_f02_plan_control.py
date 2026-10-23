"""F02-F01: model plan labels never replace trusted saved-plan context."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.plan_control import admit_plan_control, review_missing_plan_reference
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from agent.decision import Decision
from apps.api.main import create_app


def understood():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "处理当前请求", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none",
                          "question": None},
    }, ensure_ascii=False)


@pytest.mark.parametrize("intent", ["resume", "execute_first"])
@pytest.mark.parametrize("missing_reference, expected", [
    (False, None), (None, None), (True, "missing_plan"),
])
def test_host_admission_missing_checkpoint(intent, missing_reference, expected):
    assert admit_plan_control(
        PlanRequest(intent=intent), existing_checkpoint=None,
        missing_reference=missing_reference) == expected
    assert admit_plan_control(
        PlanRequest(intent=intent), existing_checkpoint={"steps": []},
        missing_reference=missing_reference) == intent


@pytest.mark.parametrize("utterance, answer", [
    ("请使用计算工具计算 (19+23)*4。", False),
    ("用 calculate 求出(19+23)*4的值", False),
    ("计算一下 19 加 23 的和再乘以四", False),
    ("请查询 MCP 中 RAG 的定义", False),
    ("现在正式创建一个任务", False),
    ("只拟定三项工作任务，不要保存", False),
    ("继续执行之前保存的计划剩余步骤", True),
    ("按之前计划只执行第一步", True),
])
def test_independent_model_reference_review(utterance, answer):
    class Provider:
        async def complete(self, **kwargs):
            return json.dumps({"explicitly_refers_to_saved_plan": answer})
    assert asyncio.run(review_missing_plan_reference(
        Provider(), "strong", utterance)) is answer


def test_invalid_reference_review_never_admits_plan():
    class Provider:
        async def complete(self, **kwargs):
            return "not-json"
    assert asyncio.run(review_missing_plan_reference(
        Provider(), "strong", "计算 19+23")) is None


def test_misclassified_standalone_tool_does_not_become_missing_plan(
    tmp_path, monkeypatch,
):
    async def complete(self, *, model, messages, response_schema=None):
        if response_schema and "explicitly_refers_to_saved_plan" in (
            response_schema.get("properties") or {}
        ):
            return '{"explicitly_refers_to_saved_plan":false}'
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)

    async def misroute(provider, model, message, existing_plan):
        assert not existing_plan
        return PlanRequest(intent="resume")
    monkeypatch.setattr("apps.api.main.review_plan_intent", misroute)

    class FakeDecider:
        def __init__(self, settings):
            pass

        async def decide(self, messages, tools):
            return Decision(content="已处理请求", tool_calls=[])

    # Keep the same production routing condition (agent_decider=None) while
    # replacing only its provider-backed model decider for offline tests.
    monkeypatch.setattr("apps.api.main.NativeToolDecision", FakeDecider)
    async def read_only(message, interpretation):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'f02.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=read_only)) as client:
        cid = client.post("/conversations").json()["id"]
        for index, message in enumerate((
            "请使用计算工具计算 (19+23)*4。",
            "请查询 MCP 中 RAG 的定义",
            "请正式创建一个任务，名字是测试任务",
        )):
            response = client.post(
                f"/conversations/{cid}/agent/messages",
                json={"content": message,
                      "client_request_id": f"f02-sta-{index:04d}"})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["termination_reason"] != "plan_not_found"
            assert data["steps"] >= 1
            assert data.get("plan_checkpoint") is None


def test_true_missing_plan_reference_remains_blocked(tmp_path, monkeypatch):
    async def complete(self, *, model, messages, response_schema=None):
        if response_schema and "explicitly_refers_to_saved_plan" in (
            response_schema.get("properties") or {}
        ):
            return '{"explicitly_refers_to_saved_plan":true}'
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)

    async def missing_plan(provider, model, message, existing_plan):
        return PlanRequest(intent="resume")
    monkeypatch.setattr("apps.api.main.review_plan_intent", missing_plan)

    class NeverDecide:
        def __init__(self, settings):
            pass

        async def decide(self, messages, tools):
            raise AssertionError("plan continuation must not reach AgentLoop")

    monkeypatch.setattr("apps.api.main.NativeToolDecision", NeverDecide)
    async def policy(message, result):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'f02missing.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(
            f"/conversations/{cid}/agent/messages",
            json={"content": "继续执行之前保存的计划剩余步骤",
                  "client_request_id": "f02-missing-0001"})
        assert response.status_code == 200
        body = response.json()
        assert body["termination_reason"] == "plan_not_found"
        assert body["steps"] == 0
        assert body.get("plan_checkpoint") is None
