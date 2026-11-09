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
                assert rows[0].tool_version == "1.0.0"
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
            assert cid_response.status_code == 201, cid_response.text
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


@pytest.mark.parametrize("mutate", [
    "server_name", "server_version", "protocol", "schema", "extra_tool",
    "valid",
])
def test_b3b_verified_mcp_sends_call_only_after_catalog_validation(monkeypatch, mutate):
    import json
    from agent.mcp_stdio_client import (
        StdioMCPClient, TRUSTED_REFERENCE_TOOL, TRUSTED_REFERENCE_SERVER,
    )

    sent = []
    server = dict(TRUSTED_REFERENCE_SERVER)
    tool = dict(TRUSTED_REFERENCE_TOOL)
    if mutate == "server_name":
        server["name"] = "untrusted-mcp"
    elif mutate == "server_version":
        server["version"] = "9.9.9"
    elif mutate == "schema":
        tool["inputSchema"] = {"type": "object", "additionalProperties": True}
    tools = [tool]
    if mutate == "extra_tool":
        tools.append({"name": "run_shell", "inputSchema": {}})

    # In-memory queue models responses from a persistent stdio process.
    import queue as queue_module
    queue = queue_module.Queue()

    class Process:
        stdin = None
        stdout = None

        def __init__(self, *args, **kwargs):
            self.stdout = self
            self.dead = False

        def __iter__(self):
            while not self.dead:
                try:
                    item = queue.get(timeout=1)
                except queue_module.Empty:
                    continue
                if item is None:
                    return
                yield json.dumps(item) + "\n"

        def poll(self):
            return None if not self.dead else -9

        def kill(self):
            self.dead = True
            queue.put(None)

        def wait(self):
            return -9

    # Input writes queue protocol replies immediately; the client reader
    # consumes them on a separate thread.
    def enqueue_write(text):
        request = json.loads(text)
        sent.append(request)
        if request.get("method") == "initialize":
            response = {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": "bad" if mutate == "protocol" else "2025-06-18",
                "serverInfo": server}}
        elif request.get("method") == "tools/list":
            response = {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}
        elif request.get("method") == "tools/call":
            response = {"jsonrpc": "2.0", "id": 3, "result": {
                "content": [{"type": "text", "text": json.dumps({
                    "ok": True, "term": "rag", "source": "trusted_mock_catalog_v1"})}],
                "isError": False}}
        else:
            return len(text)
        queue.put(response)
        return len(text)

    Process.stdin = type("FakeStdin", (), {
        "write": lambda self, text: enqueue_write(text),
        "flush": lambda self: None,
    })()
    monkeypatch.setattr("agent.mcp_stdio_client.subprocess.Popen", Process)
    result = StdioMCPClient().call_trusted_reference({"term": "rag"})
    methods = [request.get("method") for request in sent]
    if mutate == "valid":
        assert result["ok"]
        assert methods == [
            "initialize", "notifications/initialized", "tools/list", "tools/call",
        ]
    else:
        assert result == {"ok": False, "error": "mcp_catalog_mismatch"}
        assert "tools/call" not in methods


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


def test_minimal_ordinary_invoker_is_fail_closed_and_version_bound():
    from agent.tool_invoker import OrdinaryReadInvoker, ReadInvocation

    invoker = OrdinaryReadInvoker()

    def attempt(granted=(), exposed=("calculate",), version="1.0.0", arguments=None):
        return invoker.invoke(ReadInvocation(
            name="calculate",
            arguments={"expression": "19+23"} if arguments is None else arguments,
            granted_names=granted,
            exposed_names=exposed,
            expected_version=version,
        ))

    assert attempt()["error"] == "permission_denied"
    assert attempt(granted=("calculate",), exposed=())["error"] == "tool_not_exposed"
    assert attempt(granted=("calculate",), version=None)["error"] == "tool_version_mismatch"
    assert attempt(granted=("calculate",), arguments={"expression": "1+2", "extra": True})["error"] == "invalid_arguments"
    passed = attempt(granted=("calculate",))
    assert passed["ok"] is True and passed["value"] == 42
    assert passed["tool_name"] == "calculate"
    assert passed["tool_version"] == "1.0.0"


def test_minimal_invoker_does_not_authorize_write_tools():
    from agent.tool_invoker import OrdinaryReadInvoker, ReadInvocation

    result = OrdinaryReadInvoker().invoke(ReadInvocation(
        name="create_task", arguments={"title": "unsafe"},
        granted_names=("create_task",), exposed_names=("create_task",),
        expected_version="1.0.0",
    ))
    assert result == {"ok": False, "error": "unknown_tool"}


def test_plan_execution_keeps_m4_dispatch_and_does_not_reuse_ordinary_grant(monkeypatch):
    """A plan's bound call must use its own journal, not an ordinary Host read grant."""
    from agent import loop as loop_module
    from agent.planning import PlanLedger

    ledger = PlanLedger()
    assert ledger.propose([{
        "goal": "计算 19+23",
        "expected_tool": "calculate",
        "expected_arguments": {"expression": "19+23"},
    }])["ok"]

    invocations = []
    class Journal:
        def begin(self, step_id, name, arguments):
            invocations.append(("begin", step_id, name, arguments))
            return "dispatch-1"
        def record_receipt(self, dispatch_id, observation):
            invocations.append(("receipt", dispatch_id, observation))

    class NoModel:
        async def decide(self, messages, tools):
            raise AssertionError("bound plan should not ask model to dispatch")

    def bound_tool(name, arguments, tasks, clock=None, **kwargs):
        invocations.append(("execute", name, arguments))
        return {"ok": True, "value": 42}

    def forbidden_registry(*args, **kwargs):
        raise AssertionError("ordinary-only Registry must not execute saved plans")

    monkeypatch.setattr(loop_module, "execute_tool", bound_tool)
    monkeypatch.setattr(loop_module.OrdinaryReadInvoker, "invoke", forbidden_registry)
    result = asyncio.run(AgentLoop(
        NoModel(), dispatch_journal=Journal(),
        host_read_grants=("calculate",),
    ).run("执行已保存计划", plan_checkpoint=ledger.checkpoint(),
          plan_control="execute_first", execution_mode="read_only",
          include_evidence=True, execution_guard=lambda: True))
    assert result["status"] == "completed"
    assert result["plan"][0]["status"] == "completed"
    assert [item[0] for item in invocations] == ["begin", "execute", "receipt"]
    assert invocations[0][2:] == ("calculate", {"expression": "19+23"})
    assert invocations[2][1] == "dispatch-1"


def test_plan_execution_guard_failure_causes_no_dispatch(monkeypatch):
    from agent.planning import PlanLedger
    from agent import loop as loop_module

    ledger = PlanLedger()
    assert ledger.propose([{
        "goal": "计算 19+23",
        "expected_tool": "calculate",
        "expected_arguments": {"expression": "19+23"},
    }])["ok"]
    def forbidden_execute(*args, **kwargs):
        raise AssertionError("guard denial must suppress any tool call")
    monkeypatch.setattr(loop_module, "execute_tool", forbidden_execute)
    class Journal:
        def begin(self, *args):
            raise AssertionError("guard denial must suppress M4 begin")
    class NoModel:
        async def decide(self, messages, tools):
            raise AssertionError("guard denial must not ask model to dispatch")
    result = asyncio.run(AgentLoop(
        NoModel(), dispatch_journal=Journal(),
        host_read_grants=("calculate",),
    ).run("执行已保存计划", plan_checkpoint=ledger.checkpoint(),
          plan_control="execute_first", execution_mode="read_only",
          execution_guard=lambda: False))
    assert result["status"] == "partial"
    assert result["plan"][0]["status"] == "pending"
    assert any(item["type"] == "plan_execution_guard_conflict"
               for item in result["trace"])


def test_plan_invocation_inspection_binds_revision_and_args_without_executing(monkeypatch):
    from agent.plan_invocation import inspect_plan_invocations
    from agent.planning import PlanLedger
    from services.evidence_store import arguments_digest
    from agent import tool_invoker

    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": "计算", "expected_tool": "calculate",
         "expected_arguments": {"expression": "19+23"}},
        {"goal": "查询定义", "expected_tool": "mcp_reference_lookup",
         "expected_arguments": {"term": "rag"}},
    ])["ok"]

    def reject(*args, **kwargs):
        raise AssertionError("inspection cannot execute a tool")
    monkeypatch.setattr(tool_invoker.OrdinaryReadInvoker, "invoke", reject)

    first = inspect_plan_invocations(ledger.checkpoint(), "execute_first")
    all_steps = inspect_plan_invocations(ledger.checkpoint(), "resume")
    assert len(first) == 1 and len(all_steps) == 2
    assert first[0] == all_steps[0].__class__(
        plan_revision=1, step_id=1, control="execute_first",
        tool_name="calculate",
        arguments_digest=arguments_digest({"expression": "19+23"}),
        tool_version=None,
    )
    assert all_steps[1].tool_name == "mcp_reference_lookup"
    assert all(item.tool_version is None for item in all_steps)


def test_plan_invocation_inspection_rejects_unbound_malformed_and_completed():
    from agent.plan_invocation import inspect_plan_invocations
    from agent.planning import PlanLedger

    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": "合法计算", "expected_tool": "calculate",
         "expected_arguments": {"expression": "19+23"}}
    ])["ok"]
    valid = ledger.checkpoint()

    with pytest.raises(ValueError, match="invalid_plan_control"):
        inspect_plan_invocations(valid, "cancel")
    bad = {**valid, "steps": [{**valid["steps"][0],
           "expected_arguments": {"expression": "1+2", "extra": True}}]}
    with pytest.raises(ValueError, match="invalid_plan_arguments"):
        inspect_plan_invocations(bad, "resume")
    bad_tool = {**valid, "steps": [{**valid["steps"][0],
                "expected_tool": "create_task",
                "expected_arguments": {"title": "unexpected write"}}]}
    with pytest.raises(ValueError, match="invalid_plan_binding"):
        inspect_plan_invocations(bad_tool, "resume")
    assert ledger.observe("calculate", {"ok": True, "value": 42},
                          {"expression": "19+23"}) is None
    with pytest.raises(ValueError, match="plan_has_no_pending_steps"):
        inspect_plan_invocations(ledger.checkpoint(), "resume")


def test_plan_first_inspection_rejects_invalid_later_pending_step():
    from agent.plan_invocation import inspect_plan_invocations
    from agent.planning import PlanLedger

    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": "先计算", "expected_tool": "calculate",
         "expected_arguments": {"expression": "19+23"}},
        {"goal": "后计算", "expected_tool": "calculate",
         "expected_arguments": {"expression": "3*4"}},
    ])["ok"]
    checkpoint = ledger.checkpoint()
    checkpoint["steps"][1]["expected_arguments"] = {"expression": "3*4", "extra": 1}
    with pytest.raises(ValueError, match="invalid_plan_arguments"):
        inspect_plan_invocations(checkpoint, "execute_first")


def test_host_plan_binding_captures_exact_snapshot_and_turn_context():
    import json
    import hashlib
    from agent.host_plan_execution_binding import bind_host_plan_execution
    from agent.primary_host_admission_v1 import PrimaryHostDecision
    from agent.planning import PlanLedger

    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": "计算", "expected_tool": "calculate",
         "expected_arguments": {"expression": "19+23"}},
        {"goal": "查参考", "expected_tool": "mcp_reference_lookup",
         "expected_arguments": {"term": "rag"}},
    ])["ok"]
    snapshot = ledger.checkpoint()
    raw = json.dumps(snapshot, ensure_ascii=False)
    decision = PrimaryHostDecision(
        "allow", "primary_v1_plan_execution_candidate",
        "plan_execute_first", "read_only", "execute_first")
    args = dict(decision=decision, owner_id="owner-1", conversation_id="conv-1",
                turn_id="turn-1", request_id="request-1",
                checkpoint_raw=raw, checkpoint=snapshot)
    bound = bind_host_plan_execution(**args)
    assert bound.owner_id == "owner-1" and bound.turn_id == "turn-1"
    assert bound.checkpoint_digest == hashlib.sha256(raw.encode()).hexdigest()
    assert bound.plan_revision == snapshot["revision"]
    assert len(bound.invocations) == 1 and bound.invocations[0].tool_name == "calculate"
    assert bound.invocations[0].tool_version is None
    with pytest.raises(ValueError, match="host_plan_snapshot_mismatch"):
        bind_host_plan_execution(**{**args, "checkpoint": {"revision": 2}})
    with pytest.raises(ValueError, match="host_plan_identity_missing"):
        bind_host_plan_execution(**{**args, "owner_id": ""})


def test_host_plan_binding_rejects_nonplan_route_and_scope_spoofing():
    import json
    from agent.host_plan_execution_binding import bind_host_plan_execution
    from agent.primary_host_admission_v1 import PrimaryHostDecision
    from agent.planning import PlanLedger

    ledger = PlanLedger()
    assert ledger.propose([{
        "goal": "计算", "expected_tool": "calculate",
        "expected_arguments": {"expression": "19+23"},
    }])["ok"]
    checkpoint = ledger.checkpoint()
    args = dict(owner_id="owner", conversation_id="conv",
                turn_id="turn", request_id="req",
                checkpoint_raw=json.dumps(checkpoint), checkpoint=checkpoint)
    for decision in [
        PrimaryHostDecision("deny", "primary_v1_plan_execution_candidate",
                            "plan_resume", "read_only", "resume"),
        PrimaryHostDecision("allow", "primary_v1_ordinary_read_only",
                            "ordinary_tool", "read_only", None),
        PrimaryHostDecision("allow", "primary_v1_plan_execution_candidate",
                            "plan_resume", "read_only", "execute_first"),
        PrimaryHostDecision("allow", "primary_v1_plan_execution_candidate",
                            "plan_resume", "respond_only", "resume"),
    ]:
        with pytest.raises(ValueError, match="host_plan_"):
            bind_host_plan_execution(decision=decision, **args)


def test_host_plan_binding_matcher_fails_closed():
    import json
    from agent.host_plan_execution_binding import host_plan_binding_matches
    from agent.primary_host_admission_v1 import PrimaryHostDecision
    from agent.planning import PlanLedger
    ledger = PlanLedger()
    assert ledger.propose([{"goal": "Calculate", "expected_tool": "calculate",
                            "expected_arguments": {"expression": "19+23"}}])["ok"]
    checkpoint = ledger.checkpoint()
    raw = json.dumps(checkpoint)
    decision = PrimaryHostDecision(
        "allow", "primary_v1_plan_execution_candidate",
        "plan_execute_first", "read_only", "execute_first")
    base = dict(decision=decision, owner_id="owner-1", conversation_id="conv-1",
                turn_id="turn-1", request_id="request-1",
                checkpoint_raw=raw, checkpoint=checkpoint,
                expected_control="execute_first")
    assert host_plan_binding_matches(**base)
    assert not host_plan_binding_matches(**{**base, "checkpoint_raw": None})
    assert not host_plan_binding_matches(**{**base, "expected_control": "resume"})
    assert not host_plan_binding_matches(**{**base, "checkpoint": {"revision": 2}})
    assert not host_plan_binding_matches(**{**base, "owner_id": ""})


def test_http_saved_plan_binding_gate_denies_before_dispatch(tmp_path, monkeypatch):
    import json
    import apps.api.main as api
    from agent.planning import PlanLedger
    from services.db import ConversationPlan, PlanToolDispatch

    async def primary(*args, **kwargs):
        msg = args[2]
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "plan_execute_first", "quote": msg,
            "execution_scope": "first_step", "effect_intent": "read_only",
        }), msg, saved_plan_present=True, pending_action=None)

    monkeypatch.setattr(api, "propose_primary_route", primary)
    monkeypatch.setattr(api, "host_plan_binding_matches", lambda **kwargs: False)
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'plan_gate.db'}",
                   model_provider="ollama_proxy", routing_profile="unified")
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        ledger = PlanLedger()
        assert ledger.propose([
            {"goal": "Calculate", "expected_tool": "calculate",
             "expected_arguments": {"expression": "19+23"}},
        ])["ok"]
        _, Session = database(cfg.database_url)
        with Session.begin() as db:
            db.add(ConversationPlan(
                conversation_id=cid, snapshot=json.dumps(ledger.checkpoint())))
        result = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "执行已保存计划的第一步",
            "client_request_id": "plan-binding-deny-001",
        })
        assert result.status_code == 200, result.text
        assert result.json()["termination_reason"] == "primary_v1_plan_admission_denied"
        with Session() as db:
            dispatches = db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all()
        assert dispatches == []


def test_http_saved_plan_binding_allows_first_then_resume(tmp_path, monkeypatch):
    import json
    import apps.api.main as api
    from agent.planning import PlanLedger
    from services.db import ConversationPlan, PlanToolDispatch

    async def primary(*args, **kwargs):
        msg = args[2]
        first = "第一步" in msg
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "plan_execute_first" if first else "plan_resume",
            "quote": msg,
            "execution_scope": "first_step" if first else "remaining_steps",
            "effect_intent": "read_only",
        }), msg, saved_plan_present=True, pending_action=None)

    class NoModel:
        def __init__(self, settings):
            pass

        async def decide(self, messages, tools):
            raise AssertionError("saved bound execution should not invoke model")

    monkeypatch.setattr(api, "propose_primary_route", primary)
    monkeypatch.setattr(api, "NativeToolDecision", NoModel)
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'plan_positive.db'}",
                   model_provider="ollama_proxy", routing_profile="unified")
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        ledger = PlanLedger()
        assert ledger.propose([
            {"goal": "First calculation", "expected_tool": "calculate",
             "expected_arguments": {"expression": "19+23"}},
            {"goal": "Second calculation", "expected_tool": "calculate",
             "expected_arguments": {"expression": "4*5"}},
        ])["ok"]
        _, Session = database(cfg.database_url)
        with Session.begin() as db:
            db.add(ConversationPlan(
                conversation_id=cid, snapshot=json.dumps(ledger.checkpoint())))

        first = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "只执行已保存计划的第一步",
            "client_request_id": "plan-positive-first-001",
        })
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "partial"
        with Session() as db:
            record = db.get(ConversationPlan, cid)
            snapshot = json.loads(record.snapshot)
            entries = db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all()
            assert [step["status"] for step in snapshot["steps"]] == [
                "completed", "pending"]
            assert len(entries) == 1
            assert entries[0].status == "checkpointed"
            assert entries[0].tool_version is None  # M4 version not yet pinned

        resumed = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "继续执行已保存计划的剩余步骤",
            "client_request_id": "plan-positive-resume-002",
        })
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "completed"
        with Session() as db:
            record = db.get(ConversationPlan, cid)
            snapshot = json.loads(record.snapshot)
            entries = db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all()
            assert all(step["status"] == "completed" for step in snapshot["steps"])
            assert len(entries) == 2
            assert all(entry.status == "checkpointed" for entry in entries)
            assert all(entry.tool_version is None for entry in entries)


def test_tool_version_evidence_preserves_legacy_unknown_and_guarded_completion(tmp_path):
    from services.db import AgentAttempt, Conversation, Turn, database
    from services.evidence_store import AttemptEvidenceStore, make_attempt
    from sqlalchemy import select

    _, Session = database(f"sqlite:///{tmp_path / 'version_provenance.db'}")
    from services.db import Base
    engine = Session.kw["bind"]
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        conversation = Conversation(owner_id="version-provenance-owner")
        db.add(conversation)
        db.flush()
        turn = Turn(conversation_id=conversation.id, request_id="version-provenance-request")
        db.add(turn)
        db.flush()
        cid, tid = conversation.id, turn.id
    journal = AttemptEvidenceStore(cid, tid)

    old = make_attempt("run-old", "calculate", {"expression": "1+2"},
                       {"ok": True, "value": 3})
    assert old["tool_version"] is None
    with Session.begin() as db:
        journal.save_in_session(db, [old])

    current = make_attempt("run-new", "calculate", {"expression": "19+23"},
                           {"ok": False, "error": "prepared"},
                           tool_version="1.0.0")
    journal.begin(Session, current)
    current["status"] = "succeeded"
    current["error_code"] = None
    current["result_projection"] = {"ok": True, "value": 42}
    journal.finish(Session, current)
    with Session() as db:
        rows = db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all()
        mapping = {row.id: row for row in rows}
        assert mapping[old["attempt_id"]].tool_version is None
        assert mapping[current["attempt_id"]].tool_version == "1.0.0"
        assert mapping[current["attempt_id"]].status == "succeeded"

    # A different version cannot finalize the already prepared attempt.
    mismatched = make_attempt("run-other", "calculate", {"expression": "4+5"},
                              {"ok": False, "error": "prepared"},
                              tool_version="1.0.0")
    journal.begin(Session, mismatched)
    mismatched["tool_version"] = "2.0.0"
    mismatched["status"] = "succeeded"
    mismatched["result_projection"] = {"ok": True, "value": 9}
    with pytest.raises(RuntimeError, match="attempt_outcome_conflict"):
        journal.finish(Session, mismatched)


def test_new_plan_pinning_is_legacy_executor_specific_and_version_mismatch_fails_closed():
    from agent.plan_tool_versions import (
        LEGACY_PLAN_EXECUTOR_VERSION, pin_new_plan_checkpoint,
        plan_versions_compatible,
    )
    from agent.plan_invocation import inspect_plan_invocations
    from agent.planning import PlanLedger

    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": "first", "expected_tool": "calculate",
         "expected_arguments": {"expression": "19+23"}},
        {"goal": "second", "expected_tool": "calculate",
         "expected_arguments": {"expression": "4*5"}},
    ])["ok"]
    legacy = ledger.checkpoint()
    assert plan_versions_compatible(legacy, "resume")  # legacy compatibility
    pinned = pin_new_plan_checkpoint(legacy)
    assert all(s["tool_version"] == LEGACY_PLAN_EXECUTOR_VERSION
               for s in pinned["steps"])
    assert inspect_plan_invocations(pinned, "execute_first")[0].tool_version == (
        LEGACY_PLAN_EXECUTOR_VERSION)
    assert plan_versions_compatible(pinned, "execute_first")
    assert plan_versions_compatible(pinned, "resume")
    with pytest.raises(ValueError, match="plan_version_pin_requires_new_plan"):
        pin_new_plan_checkpoint(pinned)
    changed = {**pinned, "steps": [dict(s) for s in pinned["steps"]]}
    changed["steps"][1]["tool_version"] = "legacy-plan-v2"
    assert not plan_versions_compatible(changed, "execute_first")
    assert not plan_versions_compatible(changed, "resume")
    changed["steps"][1].pop("tool_version")
    assert not plan_versions_compatible(changed, "resume")
    changed["steps"][1]["tool_version"] = None
    assert not plan_versions_compatible(changed, "resume")


def test_versioned_saved_plan_journal_receipt_and_mismatched_executor_fence(monkeypatch):
    from agent.plan_tool_versions import pin_new_plan_checkpoint
    from agent.planning import PlanLedger
    from agent import loop as loop_module

    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": "first", "expected_tool": "calculate",
         "expected_arguments": {"expression": "19+23"}},
    ])["ok"]
    pinned = pin_new_plan_checkpoint(ledger.checkpoint())
    captured = []

    class Journal:
        def begin(self, step_id, name, arguments, *, tool_version=None):
            captured.append(("begin", tool_version))
            return "dispatch-1"

        def record_receipt(self, dispatch_id, result):
            captured.append(("receipt", dispatch_id))

    class NoModel:
        async def decide(self, messages, tools):
            raise AssertionError("bound saved plan should never call model")

    def tool(name, arguments, tasks, clock=None, **kwargs):
        captured.append(("execute", name))
        return {"ok": True, "value": 42}

    monkeypatch.setattr(loop_module, "execute_tool", tool)
    output = asyncio.run(AgentLoop(NoModel(), dispatch_journal=Journal()).run(
        "execute plan", plan_checkpoint=pinned,
        plan_control="execute_first", execution_mode="read_only",
        execution_guard=lambda: True))
    assert output["status"] == "completed"
    assert captured == [
        ("begin", "legacy-plan-v1"),
        ("execute", "calculate"),
        ("receipt", "dispatch-1"),
    ]

    poisoned = {**pinned, "steps": [dict(pinned["steps"][0])]}
    poisoned["steps"][0]["tool_version"] = "legacy-plan-v2"
    captured.clear()
    blocked = asyncio.run(AgentLoop(NoModel(), dispatch_journal=Journal()).run(
        "execute plan", plan_checkpoint=poisoned,
        plan_control="execute_first", execution_mode="read_only",
        execution_guard=lambda: True))
    assert blocked["termination_reason"] == "plan_tool_version_mismatch"
    assert blocked["plan"][0]["status"] == "pending"
    assert captured == []
