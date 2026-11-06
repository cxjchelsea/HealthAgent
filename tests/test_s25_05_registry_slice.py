"""S25-05 Step4 opt-in ordinary read-tool slice.

HTTP runs the real Host/AgentLoop/Attempt code; only provider decisions are
scripted. This is NOT live Qwen evidence.
"""
import asyncio
from datetime import datetime, timezone
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
    approved_read_grants, ReadToolRuntime,
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
    assert (r["tool_name"], r["tool_version"]) == ("text_stats", "1.0.0")


def test_registered_tool_cannot_execute_without_trusted_version_binding():
    executed = []
    definition = ReadToolDefinition(
        "versioned_read", "1.2.0", "A version-bound read", StrictReadArgs,
        adapter=lambda _: executed.append(True) or {"ok": True})
    registry = TrustedReadRegistry((definition,))
    common = {"granted_names": ("versioned_read",),
              "exposed_names": ("versioned_read",)}
    assert registry.invoke("versioned_read", {}, **common)["error"] == "tool_version_mismatch"
    assert registry.invoke("versioned_read", {}, expected_version="1.1.0",
                           **common)["error"] == "tool_version_mismatch"
    assert executed == []
    assert registry.invoke("versioned_read", {}, expected_version="1.2.0",
                           **common)["ok"] is True
    assert executed == [True]


def test_duplicate_definition_is_rejected():
    definition = ReadToolDefinition("x", "1.0.0", "read only", StrictReadArgs,
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


def test_model_cannot_count_fabricated_text():
    class FabricatingModel:
        async def decide(self, messages, tools):
            if any(m.get("role") == "tool" for m in messages):
                return Decision("无法核实", [])
            return Decision("", [{"id": "fabricated", "name": "text_stats",
                                  "arguments": {"text": "invented not in request"}}])
    result = asyncio.run(AgentLoop(
        FabricatingModel(), host_read_grants=("text_stats",)).run(
            MESSAGE, execution_mode="read_only", include_evidence=True))
    attempts = [a for a in result["audit"] if a["name"] == "text_stats"]
    assert len(attempts) == 1
    assert attempts[0]["observation"]["error"] == "argument_provenance_missing"
    assert not any(t.get("name") == "text_stats" and t.get("ok")
                   for t in result["trace"])



def test_calculate_registry_is_explicit_host_opt_in_and_preserves_observations():
    base = dict(host_disposition="allow", host_route="ordinary_tool",
                host_mode="read_only", feature_enabled=False)
    assert approved_read_grants(**base) == ()
    assert approved_read_grants(**base, calculate_registry_enabled=True) == ("calculate",)
    assert approved_read_grants(**{**base, "host_mode": "respond_only"},
                                calculate_registry_enabled=True) == ()
    original = READ_REGISTRY.invoke("calculate", {"expression": "(19+23)*4"},
        granted_names=("calculate",), exposed_names=("calculate",),
        expected_version="1.0.0")
    assert original["ok"] and original["value"] == 168
    for expression, error in [("1/0", "division_by_zero"),
                              ("abc", "unsupported_expression")]:
        result = READ_REGISTRY.invoke("calculate", {"expression": expression},
            granted_names=("calculate",), exposed_names=("calculate",),
            expected_version="1.0.0")
        assert result["error"] == error
    assert READ_REGISTRY.invoke("calculate", {"expression": "1+2"},
        granted_names=(), exposed_names=("calculate",),
        expected_version="1.0.0")["error"] == "permission_denied"


@pytest.mark.parametrize("enabled", [False, True])
def test_calculator_migration_preserves_tool_exposure_and_observation(enabled):
    class CalculatorModel:
        def __init__(self):
            self.seen = []
        async def decide(self, messages, tools):
            names = [t["function"]["name"] for t in tools]
            self.seen.append(names)
            if any(m.get("role") == "tool" for m in messages):
                return Decision("已计算", [])
            return Decision("", [{"id": "calc_s25_05", "name": "calculate",
                                  "arguments": {"expression": "(19+23)*4"}}])
    model = CalculatorModel()
    result = asyncio.run(AgentLoop(model,
        host_read_grants=("calculate",) if enabled else ()).run(
            "请计算 (19+23)*4", execution_mode="read_only", include_evidence=True))
    assert all(names.count("calculate") == 1 for names in model.seen)
    attempts = [a for a in result["audit"] if a["name"] == "calculate"]
    assert len(attempts) == 1
    assert attempts[0]["observation"]["ok"]
    assert attempts[0]["observation"]["value"] == 168
    assert ("tool_version" in attempts[0]["observation"]) is enabled


def test_migrated_calculator_does_not_reappear_after_semantic_filter():
    class NoTools:
        async def decide(self, messages, tools):
            assert not any(t["function"]["name"] == "calculate" for t in tools)
            return Decision("仅答复", [])
    # The existing explicit Host allowlist is a reduction. A registry grant
    # cannot revive a tool removed from this turn's exposure.
    result = asyncio.run(AgentLoop(
        NoTools(), host_read_grants=("calculate",)).run(
            "请问现在有什么工具？", execution_mode="read_only",
            tool_allowlist=()))
    assert result["status"] == "completed"


@pytest.mark.parametrize("name,enabled_flag", [
    ("utc_time", "utc_time_registry_enabled"),
    ("list_tasks", "list_tasks_registry_enabled"),
])
def test_b2_host_policy_requires_individual_opt_in(name, enabled_flag):
    base = {"host_disposition": "allow", "host_route": "ordinary_tool",
            "host_mode": "read_only", "feature_enabled": False}
    assert name not in approved_read_grants(**base)
    assert approved_read_grants(**base, **{enabled_flag: True}) == (name,)
    for change in ({"host_disposition": "deny"}, {"host_route": "respond"},
                   {"host_mode": "execute"}):
        assert approved_read_grants(**{**base, **change},
                                     **{enabled_flag: True}) == ()


def test_b2_utc_time_preserves_injected_clock_and_denies_missing_context():
    now = datetime(2026, 11, 6, 7, 30, tzinfo=timezone.utc)
    common = dict(granted_names=("utc_time",), exposed_names=("utc_time",),
                  expected_version="1.0.0")
    assert READ_REGISTRY.invoke("utc_time", {}, **common)["error"] == "tool_runtime_unavailable"
    result = READ_REGISTRY.invoke("utc_time", {}, runtime=ReadToolRuntime(clock=lambda: now),
                                  **common)
    assert result["ok"] and result["utc"] == "2026-11-06T07:30:00+00:00"
    assert READ_REGISTRY.invoke("utc_time", {"extra": True},
                                runtime=ReadToolRuntime(clock=lambda: now),
                                **common)["error"] == "invalid_arguments"


def test_b2_list_tasks_never_reads_another_conversation_or_global_store():
    class ScopedStore:
        def __init__(self, label):
            self.label = label
            self.calls = 0

        def list(self):
            self.calls += 1
            return {"ok": True, "tasks": [{"title": self.label}]}

    a, b = ScopedStore("owner-a-task"), ScopedStore("owner-b-task")
    common = dict(granted_names=("list_tasks",), exposed_names=("list_tasks",),
                  expected_version="1.0.0")
    assert READ_REGISTRY.invoke("list_tasks", {}, **common)["error"] == "tool_runtime_unavailable"
    first = READ_REGISTRY.invoke("list_tasks", {}, runtime=ReadToolRuntime(task_store=a),
                                 **common)
    second = READ_REGISTRY.invoke("list_tasks", {}, runtime=ReadToolRuntime(task_store=b),
                                  **common)
    assert first["tasks"] == [{"title": "owner-a-task"}]
    assert second["tasks"] == [{"title": "owner-b-task"}]
    assert a.calls == b.calls == 1
    denied = READ_REGISTRY.invoke("list_tasks", {}, runtime=ReadToolRuntime(task_store=a),
                                  granted_names=(), exposed_names=("list_tasks",),
                                  expected_version="1.0.0")
    assert denied["error"] == "permission_denied" and a.calls == 1


@pytest.mark.parametrize("name,arguments,expected_field", [
    ("utc_time", {}, "utc"),
    ("list_tasks", {}, "tasks"),
])
@pytest.mark.parametrize("enabled", [False, True])
def test_b2_agentloop_registry_parity_and_no_duplicate_tools(name, arguments,
                                                               expected_field, enabled):
    class ScopedStore:
        def list(self):
            return {"ok": True, "tasks": [{"task_id": 1, "title": "only-this-conversation"}]}

    class OneCall:
        def __init__(self):
            self.seen = []
        async def decide(self, messages, tools):
            names = [t["function"]["name"] for t in tools]
            self.seen.append(names)
            if any(m.get("role") == "tool" for m in messages):
                return Decision("完成", [])
            return Decision("", [{"id": "b2", "name": name, "arguments": arguments}])

    model = OneCall()
    result = asyncio.run(AgentLoop(
        model, clock=lambda: datetime(2026, 11, 6, 7, 30, tzinfo=timezone.utc),
        task_store=ScopedStore(), host_read_grants=(name,) if enabled else ()
    ).run("请读取当前会话任务或UTC时间", execution_mode="read_only",
          include_evidence=True))
    assert all(names.count(name) == 1 for names in model.seen)
    audit = [x for x in result["audit"] if x["name"] == name]
    assert len(audit) == 1 and audit[0]["observation"]["ok"]
    assert expected_field in audit[0]["observation"]
    assert ("tool_version" in audit[0]["observation"]) is enabled
