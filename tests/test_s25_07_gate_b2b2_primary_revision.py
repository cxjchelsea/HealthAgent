"""S25-07 B2b2: ONE Primary proposal/Host; no revision CAS or tools."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.primary_route_v1 import (
    PrimaryRouteRejected, RouteSignal, bind_route_signal,
    primary_route_generation_schema, propose_primary_route,
)
from agent.primary_host_admission_v1 import PrimaryHostFacts, admit_primary_route_v1
from apps.api.main import create_app
from services.db import (
    AgentAttempt, AgentTaskState, ConversationPlan, Message,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)

MESSAGE = "请将第二步的说明改为统计定义的字符数，暂不执行"
GOAL = "统计定义的字符数"


def parent():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询 RAG", "depends_on": [], "status": "pending",
             "expected_tool": "mcp_reference_lookup", "tool_version": "1.0.0",
             "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}}},
            {"id": 2, "goal": "统计定义", "depends_on": [1], "status": "pending",
             "expected_tool": "text_stats", "tool_version": "1.0.0",
             "argument_bindings": {"text": {
                 "kind": "observation_ref", "source_step_id": 1, "field": "definition"}}},
        ],
        "executions": {},
    }


def raw_parent():
    return json.dumps(parent(), ensure_ascii=False)


def signal_payload(*, changed=True, target=2, mutate=None):
    steps = copy.deepcopy(parent()["steps"])
    if changed:
        steps[1]["goal"] = GOAL
    if mutate:
        mutate(steps)
    return {
        "schema_version": "primary-route-v1",
        "route_kind": "plan_revise",
        "quote": MESSAGE,
        "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "step_number": target,
        "revision_old_argument": None,
        "revision_new_argument": None,
        "dependency_revision_steps": steps,
    }


def bind(payload=None, *, enabled=True, raw=None):
    return bind_route_signal(
        RouteSignal.model_validate(payload or signal_payload()),
        MESSAGE, saved_plan_present=True, pending_action=None,
        saved_step_count=2, dependency_revision_enabled=enabled,
        saved_plan_raw=raw or raw_parent())


def host(proposal, *, enabled=True, raw=None, message=MESSAGE):
    snap = raw or raw_parent()
    return admit_primary_route_v1(
        proposal, PrimaryHostFacts(
            checkpoint=json.loads(snap), checkpoint_raw=snap,
            pending_action=None, pending_origin_verified=False,
            guarded_mode=True, turn_created=True,
            current_request=message, dependency_plan_enabled=True,
            dependency_revision_enabled=enabled))


def test_valid_primary_revision_is_only_host_proposal():
    result = host(bind())
    assert (result.disposition, result.reason, result.execution_mode) == (
        "allow", "dependency_revision_candidate", "none")
    assert result.plan_control is None


def test_default_off_has_no_generated_revision_payload_and_is_denied():
    schema = primary_route_generation_schema(
        MESSAGE, saved_plan_present=True, dependency_revision_enabled=False)
    assert "dependency_revision_steps" not in schema["properties"]
    with pytest.raises(PrimaryRouteRejected, match="dependency_revision_payload_denied"):
        bind(enabled=False)
    assert host(bind(), enabled=False).disposition == "deny"


def test_generation_schema_advertises_suffix_only_when_requested():
    schema = primary_route_generation_schema(
        MESSAGE, saved_plan_present=True, dependency_revision_enabled=True)
    assert "dependency_revision_steps" in schema["properties"]
    assert "dependency_plan" not in schema["properties"]
    assert "DependencyStep" in schema["$defs"]


@pytest.mark.parametrize("mutation", [
    lambda steps: steps[0].update(goal="篡改第一步"),
    lambda steps: steps[1].update(depends_on=[]),
    lambda steps: steps[1].update(expected_tool="mcp_reference_lookup",
                                  argument_bindings={"term": {
                                      "kind": "literal", "value": "RAG",
                                      "source_quote": "RAG"}}),
    lambda steps: steps[1].update(tool_version="2.0.0"),
])
def test_reject_unrequested_or_untrusted_changes(mutation):
    with pytest.raises(PrimaryRouteRejected):
        bind(signal_payload(mutate=mutation))


def test_target_mismatch_and_no_change_denied():
    with pytest.raises(PrimaryRouteRejected, match="dependency_revision_target_unbound"):
        bind(signal_payload(target=1))
    with pytest.raises(PrimaryRouteRejected, match="dependency_revision_no_change"):
        bind(signal_payload(changed=False))


def test_host_rebinds_not_just_trusts_the_bound_object():
    proposal = bind()
    snap = raw_parent()
    altered = json.loads(snap)
    altered["steps"][1]["expected_tool"] = "other"
    assert host(proposal, raw=json.dumps(altered, ensure_ascii=False)).disposition == "deny"
    assert host(proposal, message="完全不同的请求").disposition == "deny"


def test_model_primary_one_call_and_saved_parent_bound():
    class Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            schema = kwargs["response_schema"]
            assert "dependency_revision_steps" in schema["properties"]
            assert "dependency_plan" not in schema["properties"]
            return json.dumps(signal_payload(), ensure_ascii=False)

    import asyncio
    provider = Provider()
    result = asyncio.run(propose_primary_route(
        provider, "fake", MESSAGE, saved_plan_present=True,
        saved_step_count=2, dependency_revision_enabled=True,
        saved_plan_raw=raw_parent()))
    assert provider.calls == 1
    assert host(result).disposition == "allow"


def test_http_revision_admission_durable_but_parent_unchanged(tmp_path, monkeypatch):
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / 'b2b2.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
        s25_07_dependency_revision_proposal_enabled=True)
    calls = []
    async def model(*args, **kwargs):
        calls.append(kwargs)
        return bind(raw=kwargs["saved_plan_raw"])

    def no_tool(*args, **kwargs):
        raise AssertionError("revision admission cannot dispatch tools")

    monkeypatch.setattr("apps.api.main.propose_primary_route", model)
    monkeypatch.setattr("agent.tool_registry.READ_REGISTRY.invoke", no_tool)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        raw = raw_parent()
        with Session.begin() as db:
            db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "b2b2-http-001"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["termination_reason"] == "dependency_revision_admitted_not_saved"
        assert result["status"] == "partial"
        with Session() as db:
            assert db.get(ConversationPlan, cid).snapshot == raw
            assert db.get(AgentTaskState, cid) is None
            assert db.query(AgentAttempt).filter_by(conversation_id=cid).count() == 0
            assert db.query(PlanToolDispatch).filter_by(conversation_id=cid).count() == 0
            evidence = db.get(PrimaryRouteEvidence, result["turn_id"])
            assert evidence.disposition == "allow"
            assert evidence.admission_reason == "dependency_revision_candidate"
            turn = db.get(Turn, result["turn_id"])
            assert turn.user_message_id is not None
            assert db.get(Message, turn.user_message_id).content == MESSAGE
        replay = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "b2b2-http-001"})
        assert replay.status_code == 409
        assert len(calls) == 1
        assert calls[0]["dependency_revision_enabled"] is True
        assert calls[0]["saved_plan_raw"] == raw


def test_host_refuses_fake_proposal_even_when_primary_flag_on(tmp_path):
    forged = bind()
    assert host(forged, raw=json.dumps({**parent(), "revision": 2})).disposition == "deny"


def test_host_denies_text_stats_literal_even_if_user_quoted():
    request = MESSAGE
    payload = signal_payload()
    payload["dependency_revision_steps"][1]["argument_bindings"]["text"] = {
        "kind": "literal", "value": "字符数", "source_quote": "字符数"}
    proposed = bind_route_signal(
        RouteSignal.model_validate(payload), request,
        saved_plan_present=True, pending_action=None,
        saved_step_count=2, dependency_revision_enabled=True,
        saved_plan_raw=raw_parent())
    result = host(proposed)
    assert (result.disposition, result.reason) == (
        "deny", "dependency_revision_binding_not_executable")


def test_host_denies_unbound_revision_under_disabled_flag_even_if_forged():
    forged = bind()
    result = admit_primary_route_v1(forged, PrimaryHostFacts(
        checkpoint=parent(), checkpoint_raw=raw_parent(),
        pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True,
        current_request=MESSAGE, dependency_plan_enabled=True,
        dependency_revision_enabled=False))
    assert (result.disposition, result.reason) == (
        "deny", "dependency_revision_host_denied")
