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



@pytest.mark.parametrize("enabled", [False, True])
def test_b2_http_persisted_task_list_is_conversation_scoped(
        tmp_path, monkeypatch, enabled):
    from services.task_store import ConversationTaskStore
    from services.db import ConversationTask

    async def primary(*args, **kwargs):
        msg = args[2]
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
            "quote": msg, "execution_scope": "none", "effect_intent": "read_only",
        }), msg, saved_plan_present=False, pending_action=None)

    class ListProvider:
        def __init__(self, settings):
            self.seen = []

        async def decide(self, messages, tools):
            names = [x["function"]["name"] for x in tools]
            self.seen.append(names)
            if any(m.get("role") == "tool" for m in messages):
                return Decision("任务已读取", [])
            return Decision("", [{"id": "b2_list", "name": "list_tasks",
                                  "arguments": {}}])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.NativeToolDecision", ListProvider)
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'b2_scoped_http.db'}",
                   model_provider="ollama_proxy", routing_profile="unified",
                   s25_05_list_tasks_registry_enabled=enabled)
    with TestClient(create_app(cfg)) as client:
        first = client.post("/conversations").json()["id"]
        second = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        ConversationTaskStore(Session, first).create("private-first")
        ConversationTaskStore(Session, second).create("private-second")
        for cid, title, forbidden in (
            (first, "private-first", "private-second"),
            (second, "private-second", "private-first"),
        ):
            response = client.post(
                f"/conversations/{cid}/agent/messages",
                json={"content": "查询当前会话的任务列表",
                      "client_request_id": f"b2-scoped-{cid}"})
            assert response.status_code == 200, response.text
            with Session() as db:
                attempts = db.scalars(select(AgentAttempt).where(
                    AgentAttempt.conversation_id == cid,
                    AgentAttempt.tool_name == "list_tasks")).all()
                assert len(attempts) == 1
                assert attempts[0].status == "succeeded"
                # The bounded observation projection must not contain
                # unrelated conversation data.
                assert forbidden not in (attempts[0].result_projection or "")
                assert title in (attempts[0].result_projection or "")
            assert response.json()["status"] in ("completed", "partial")



def test_b3_recall_memory_requires_host_grant_and_trusted_store():
    base = dict(host_disposition="allow", host_route="ordinary_tool",
                host_mode="read_only", feature_enabled=False)
    assert approved_read_grants(**base) == ()
    assert approved_read_grants(**base, recall_memory_registry_enabled=True) == ("recall_memory",)
    assert approved_read_grants(**{**base, "host_disposition": "deny"},
                                recall_memory_registry_enabled=True) == ()
    common = dict(granted_names=("recall_memory",),
                  exposed_names=("recall_memory",), expected_version="1.0.0")
    args = {"key": "test-key"}
    missing = READ_REGISTRY.invoke("recall_memory", args, **common)
    assert missing["error"] == "tool_runtime_unavailable"
    no_store = READ_REGISTRY.invoke("recall_memory", args, **common,
                                    runtime=ReadToolRuntime())
    assert no_store["error"] == "memory_unavailable"
    denied = READ_REGISTRY.invoke("recall_memory", args,
        granted_names=(), exposed_names=("recall_memory",),
        expected_version="1.0.0", runtime=ReadToolRuntime())
    assert denied["error"] == "permission_denied"


def test_b3_recall_memory_real_database_is_owner_scoped(tmp_path):
    from services.memory_store import MemoryStore
    # Instantiate the same schema bootstrapping used by the test application.
    from services.db import Base
    engine, Session = database(f"sqlite:///{tmp_path / 'b3_owner_memory.db'}")
    Base.metadata.create_all(engine)
    alice = MemoryStore(Session, "b3-alice", write_allowed=True)
    bob = MemoryStore(Session, "b3-bob", write_allowed=True)
    assert alice.put("shared-key", "only-alice")["ok"]
    assert bob.put("shared-key", "only-bob")["ok"]
    allowed = dict(granted_names=("recall_memory",), exposed_names=("recall_memory",),
                   expected_version="1.0.0")
    for store, expected, excluded in (
        (alice, "only-alice", "only-bob"),
        (bob, "only-bob", "only-alice"),
    ):
        result = READ_REGISTRY.invoke("recall_memory", {"key": "shared-key"},
                                      runtime=ReadToolRuntime(memory_store=store),
                                      **allowed)
        assert result["ok"] and result["found"]
        assert result["value"] == expected and result["value"] != excluded
        assert result["tool_version"] == "1.0.0"
    assert READ_REGISTRY.invoke("recall_memory", {"key": "shared-key", "value": "write"},
        runtime=ReadToolRuntime(memory_store=alice),
        **allowed)["error"] == "invalid_arguments"


@pytest.mark.parametrize("enabled", [False, True])
def test_b3_agentloop_recall_parity_without_duplicate_exposure(enabled):
    class ScopedMemory:
        def get(self, key):
            return {"ok": True, "found": True, "key": key, "value": "owner-secret"}

    class RecallOnce:
        def __init__(self):
            self.exposures = []
        async def decide(self, messages, tools):
            names = [t["function"]["name"] for t in tools]
            self.exposures.append(names)
            if any(m.get("role") == "tool" for m in messages):
                return Decision("已核对", [])
            return Decision("", [{"id": "b3-recall", "name": "recall_memory",
                                  "arguments": {"key": "my-key"}}])
    model = RecallOnce()
    result = asyncio.run(AgentLoop(
        model, memory_store=ScopedMemory(),
        host_read_grants=("recall_memory",) if enabled else ()
    ).run("查询记忆键 my-key", execution_mode="read_only", include_evidence=True))
    assert all(names.count("recall_memory") == 1 for names in model.exposures)
    observations = [a["observation"] for a in result["audit"] if a["name"] == "recall_memory"]
    assert len(observations) == 1 and observations[0]["ok"]
    assert observations[0]["value"] == "owner-secret"
    assert ("tool_version" in observations[0]) is enabled



@pytest.mark.parametrize("enabled", [False, True])
def test_b3_http_owner_memory_isolation_and_attempt_redaction(
        tmp_path, monkeypatch, enabled):
    """Real FastAPI authentication, owner-bound MemoryStore and SQL Attempt."""
    async def primary(*args, **kwargs):
        msg = args[2]
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
            "quote": msg, "execution_scope": "none", "effect_intent": "read_only",
        }), msg, saved_plan_present=False, pending_action=None)

    class RecallProvider:
        def __init__(self, settings):
            self.seen = []

        async def decide(self, messages, tools):
            names = [t["function"]["name"] for t in tools]
            self.seen.append(names)
            if any(m.get("role") == "tool" for m in messages):
                return Decision("已查询", [])
            return Decision("", [{"id": "b3-memory", "name": "recall_memory",
                                  "arguments": {"key": "b3-shared"}}])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.NativeToolDecision", RecallProvider)
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'b3_http_owners.db'}",
                   model_provider="ollama_proxy", routing_profile="unified",
                   allow_demo_identity=False, user_tokens="alice-token:alice,bob-token:bob",
                   s25_05_recall_memory_registry_enabled=enabled)
    with TestClient(create_app(cfg)) as client:
        owners = []
        for name, token, secret in [
            ("alice", "alice-token", "alice-private-qa-123"),
            ("bob", "bob-token", "bob-private-qa-456"),
        ]:
            headers = {"Authorization": "Bearer " + token}
            response = client.put("/memories/b3-shared", headers=headers,
                                  json={"value": secret})
            assert response.status_code == 200, response.text
            cid_response = client.post("/conversations", headers=headers)
            assert cid_response.status_code == 200, cid_response.text
            owners.append((name, headers, cid_response.json()["id"], secret))
        _, Session = database(cfg.database_url)
        for name, headers, cid, secret in owners:
            other_secret = next(value for other, _, _, value in owners if other != name)
            response = client.post(
                f"/conversations/{cid}/agent/messages", headers=headers,
                json={"content": "查询我的记忆键 b3-shared",
                      "client_request_id": "b3-read-" + name + "-001"})
            assert response.status_code == 200, response.text
            with Session() as db:
                attempts = db.scalars(select(AgentAttempt).where(
                    AgentAttempt.conversation_id == cid,
                    AgentAttempt.tool_name == "recall_memory")).all()
                assert len(attempts) == 1
                attempt = attempts[0]
                assert attempt.status == "succeeded"
                import json
                assert json.loads(attempt.result_projection) == {"ok": True, "found": True}
                assert secret not in (attempt.result_projection or "")
                assert other_secret not in (attempt.result_projection or "")
            assert any(t.get("name") == "recall_memory" and t.get("ok")
                       for t in response.json()["trace"] if t.get("type") == "tool")
            assert other_secret not in response.text
        # Conversation access must be checked before the AgentLoop sees it.
        cross = client.post(
            f"/conversations/{owners[1][2]}/agent/messages",
            headers=owners[0][1],
            json={"content": "查询我的记忆键 b3-shared",
                  "client_request_id": "b3-cross-owner-denial"})
        assert cross.status_code == 404



def test_b3b_mcp_host_grant_and_untrusted_client_are_rejected():
    base = dict(host_disposition="allow", host_route="ordinary_tool",
                host_mode="read_only", feature_enabled=False)
    assert approved_read_grants(**base) == ()
    assert approved_read_grants(**base, mcp_reference_registry_enabled=True) == (
        "mcp_reference_lookup",)
    assert approved_read_grants(**{**base, "host_disposition": "deny"},
                                mcp_reference_registry_enabled=True) == ()
    common = dict(granted_names=("mcp_reference_lookup",),
                  exposed_names=("mcp_reference_lookup",),
                  expected_version="1.0.0")
    from agent.mock_mcp import MockMCPClient
    assert READ_REGISTRY.invoke("mcp_reference_lookup", {"term": "rag"},
                               **common)["error"] == "tool_runtime_unavailable"
    assert READ_REGISTRY.invoke("mcp_reference_lookup", {"term": "rag"},
           runtime=ReadToolRuntime(mcp_client=MockMCPClient()),
           **common)["error"] == "mcp_untrusted_client"
    assert READ_REGISTRY.invoke("mcp_reference_lookup",
           {"term": "rag", "command": "echo injected"},
           runtime=ReadToolRuntime(mcp_client=MockMCPClient()),
           **common)["error"] == "invalid_arguments"


def test_b3b_trusted_local_mcp_call_preserves_observations():
    from agent.mcp_stdio_client import StdioMCPClient
    client = StdioMCPClient(timeout_seconds=5)
    common = dict(granted_names=("mcp_reference_lookup",),
                  exposed_names=("mcp_reference_lookup",),
                  expected_version="1.0.0",
                  runtime=ReadToolRuntime(mcp_client=client))
    success = READ_REGISTRY.invoke("mcp_reference_lookup", {"term": "rag"}, **common)
    assert success["ok"] and success["term"] == "rag"
    assert success["source"] == "trusted_mock_catalog_v1"
    assert success["tool_version"] == "1.0.0"
    absent = READ_REGISTRY.invoke("mcp_reference_lookup", {"term": "unknown-key"}, **common)
    assert absent["error"] == "reference_not_found"
    denied = READ_REGISTRY.invoke("mcp_reference_lookup", {"term": "rag"},
        granted_names=(), exposed_names=("mcp_reference_lookup",),
        expected_version="1.0.0", runtime=ReadToolRuntime(mcp_client=client))
    assert denied["error"] == "permission_denied"


@pytest.mark.parametrize("mutate", ["server_name", "server_version",
                                      "schema", "extra_tool"])
def test_b3b_remote_tool_discovery_cannot_authorize_catalog(monkeypatch, mutate):
    import json
    from types import SimpleNamespace
    from agent.mcp_stdio_client import StdioMCPClient, TRUSTED_REFERENCE_TOOL
    tool = {"name": TRUSTED_REFERENCE_TOOL["name"],
            "inputSchema": TRUSTED_REFERENCE_TOOL["inputSchema"],
            "description": "untrusted description"}
    server = {"name": "healthagent-trusted-reference", "version": "1.0.0"}
    if mutate == "server_name":
        server["name"] = "malicious-mcp"
    if mutate == "server_version":
        server["version"] = "9.9.9"
    if mutate == "schema":
        tool["inputSchema"] = {"type": "object", "additionalProperties": True}
    tools = [tool]
    if mutate == "extra_tool":
        tools.append({"name": "run_shell", "inputSchema": {}})
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2025-06-18", "serverInfo": server}},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}},
        {"jsonrpc": "2.0", "id": 3, "result": {
            "content": [{"type": "text", "text": json.dumps({
                "ok": True, "term": "rag", "source": "untrusted"})}],
            "isError": False}},
    ]
    def scripted_run(*args, **kwargs):
        return SimpleNamespace(returncode=0,
            stdout="".join(json.dumps(frame) + "\\n" for frame in responses))
    monkeypatch.setattr("agent.mcp_stdio_client.subprocess.run", scripted_run)
    result = StdioMCPClient().call_trusted_reference({"term": "rag"})
    assert result == {"ok": False, "error": "mcp_catalog_mismatch"}


@pytest.mark.parametrize("enabled", [False, True])
def test_b3b_agentloop_fixed_mcp_registry_parity(enabled):
    from agent.mcp_stdio_client import StdioMCPClient
    class LookupOnce:
        def __init__(self):
            self.exposures = []
        async def decide(self, messages, tools):
            names = [t["function"]["name"] for t in tools]
            self.exposures.append(names)
            if any(m.get("role") == "tool" for m in messages):
                return Decision("查阅结束", [])
            return Decision("", [{"id": "mcp-b3b", "name": "mcp_reference_lookup",
                                  "arguments": {"term": "rag"}}])
    model = LookupOnce()
    result = asyncio.run(AgentLoop(model, mcp_client=StdioMCPClient(),
        host_read_grants=("mcp_reference_lookup",) if enabled else ()
        ).run("查询 MCP 可信目录中的 rag", execution_mode="read_only",
              include_evidence=True))
    assert all(names.count("mcp_reference_lookup") == 1 for names in model.exposures)
    observations = [x["observation"] for x in result["audit"]
                    if x["name"] == "mcp_reference_lookup"]
    assert len(observations) == 1 and observations[0]["ok"]
    assert observations[0]["term"] == "rag"
    assert ("tool_version" in observations[0]) is enabled
