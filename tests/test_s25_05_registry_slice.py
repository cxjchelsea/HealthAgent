"""S25-05 Step4 opt-in ordinary read-tool slice.

HTTP runs the real Host/AgentLoop/Attempt code; only provider decisions are
scripted. This is NOT live Qwen evidence.
"""
import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from services.db import AgentAttempt, database
from pydantic import ValidationError

from agent.config import Settings
from agent.decision import Decision
from agent.loop import AgentLoop
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from agent.tool_registry import (
    READ_REGISTRY, ReadToolDefinition, StrictReadArgs, TrustedReadRegistry,
    approved_read_grants,
)
from apps.api.main import create_app


MESSAGE = "请统计这段文字的字符数和单词数：Hello world"


def test_registry_rejects_untrusted_and_invalid_calls():
    assert READ_REGISTRY.exposed(()) == []
    assert READ_REGISTRY.exposed(("text_stats",))[0]["function"]["name"] == "text_stats"
    args = {"text": "Hello world"}
    assert READ_REGISTRY.invoke("text_stats", args, granted_names=(),
                                exposed_names=("text_stats",))["error"] == "permission_denied"
    assert READ_REGISTRY.invoke("text_stats", args, granted_names=("text_stats",),
                                exposed_names=())["error"] == "tool_not_exposed"
    assert READ_REGISTRY.invoke("text_stats", {"text": "x", "extra": "y"},
                                granted_names=("text_stats",),
                                exposed_names=("text_stats",))["error"] == "invalid_arguments"
    assert READ_REGISTRY.invoke("text_stats", args, granted_names=("text_stats",),
                                exposed_names=("text_stats",),
                                expected_version="2.0.0")["error"] == "tool_version_mismatch"
    assert READ_REGISTRY.invoke("missing", {}, granted_names=("missing",),
                                exposed_names=("missing",))["error"] == "unknown_tool"
    r = READ_REGISTRY.invoke("text_stats", args, granted_names=("text_stats",),
                             exposed_names=("text_stats",), expected_version="1.0.0")
    assert r["ok"] and r["characters"] == 11 and r["words"] == 2


def test_duplicate_definition_is_rejected():
    definition = ReadToolDefinition("x", "1", "read only", StrictReadArgs,
                                    adapter=lambda _: {"ok": True})
    with pytest.raises(ValueError, match="duplicate_tool_registration"):
        TrustedReadRegistry((definition, definition))


@pytest.mark.parametrize("changed", [
    {"host_disposition": "deny"},
    {"host_route": "respond"},
    {"host_mode": "none"},
    {"feature_enabled": False},
])
def test_host_policy_fails_closed(changed):
    args = {"host_disposition": "allow", "host_route": "ordinary_tool",
            "host_mode": "read_only", "feature_enabled": True}
    assert approved_read_grants(**(args | changed)) == ()
    assert approved_read_grants(**args) == ("text_stats",)


class Script:
    def __init__(self):
        self.tools = []
    async def decide(self, messages, tools):
        names = [x["function"]["name"] for x in tools]
        self.tools.append(names)
        if any(m.get("role") == "tool" for m in messages):
            return Decision("统计完成", [])
        if "text_stats" not in names:
            return Decision("不能执行该工具", [])
        return Decision("", [{"id": "stats_1", "name": "text_stats",
                              "arguments": {"text": "Hello world"}}])


@pytest.mark.parametrize("enabled", [False, True])
def test_agentloop_registered_read_requires_host_grant(enabled):
    model = Script()
    grants = ("text_stats",) if enabled else ()
    result = asyncio.run(AgentLoop(model, host_read_grants=grants).run(
        MESSAGE, execution_mode="read_only", include_evidence=True))
    assert ("text_stats" in model.tools[0]) is enabled
    if enabled:
        assert any(t["type"] == "tool" and t["name"] == "text_stats" and t["ok"]
                   for t in result["trace"])
        assert any(a["name"] == "text_stats" and a["observation"]["characters"] == 11
                   for a in result["audit"])
    else:
        assert not any(t.get("name") == "text_stats" for t in result["trace"])


@pytest.mark.parametrize("enabled", [False, True])
def test_http_primary_host_readonly_grant_and_attempt(tmp_path, monkeypatch, enabled):
    async def primary(*args, **kwargs):
        msg = args[2]
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
            "quote": msg, "execution_scope": "none", "effect_intent": "read_only",
        }), msg, saved_plan_present=False, pending_action=None)

    class ScriptedProvider:
        def __init__(self, settings):
            self.script = Script()
        async def decide(self, messages, tools):
            return await self.script.decide(messages, tools)

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.NativeToolDecision", ScriptedProvider)
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'tool_registry.db'}",
                   model_provider="ollama_proxy", routing_profile="unified",
                   s25_05_text_stats_enabled=enabled)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": MESSAGE, "client_request_id": "s25-05-read-http-001"})
        assert response.status_code == 200, response.text
        trace = response.json()["trace"]
        assert any(t.get("name") == "text_stats" and t.get("ok")
                   for t in trace if t.get("type") == "tool") is enabled
        _, Session = database(cfg.database_url)
        with Session() as db:
            rows = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            if enabled:
                assert len(rows) == 1
                assert rows[0].tool_name == "text_stats"
                assert rows[0].status == "succeeded"
                import json
                projection = json.loads(rows[0].result_projection)
                assert projection == {"ok": True, "characters": 11, "words": 2}
                assert "Hello world" not in rows[0].result_projection
            else:
                assert rows == []
        replay = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": MESSAGE, "client_request_id": "s25-05-read-http-001"})
        assert replay.status_code == 409


def test_host_grant_does_not_enable_respond_or_saved_plan():
    class OneCall:
        async def decide(self, messages, tools):
            assert not any(t["function"]["name"] == "text_stats" for t in tools)
            return Decision("不执行工具", [])
    for mode in ("respond_only",):
        result = asyncio.run(AgentLoop(OneCall(), host_read_grants=("text_stats",)).run(
            MESSAGE, execution_mode=mode))
        assert result["status"] == "completed"
