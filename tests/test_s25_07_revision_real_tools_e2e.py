"""S25-07 two-Turn integration: both tools use actual trusted adapters.

Turn A starts the checked-in trusted MCP stdio child process; Turn B invokes
the real text_stats registry adapter over its durable source Receipt.
This is a *fixture-authorized internal* E2E, not an HTTP/model-routing test.
Runs on SQLite and isolated CI PostgreSQL.
"""
import json

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from agent.mcp_stdio_client import StdioMCPClient
from agent.tool_registry import READ_REGISTRY
from services.db import (
    AgentAttempt, AgentTaskState, ConversationPlan, DependencyWorkerFence,
    Message, PlanExecutionClaim, PlanToolDispatch, Turn,
)
from services.dependency_fencing import acquire_dependency_fence
from services.dependency_revision_continuation import (
    admit_revised_continuation_claim_in_session,
)
from services.dependency_revision_first_mcp_runner import (
    invoke_first_revised_mcp_reference,
)
from services.dependency_revision_registry_runner import (
    invoke_second_revised_registry_read,
)
from services.dependency_revision_result import (
    checkpoint_revised_step_in_session,
)
from services.dependency_revision_second import (
    begin_second_revised_dispatch_in_session,
    checkpoint_second_revised_step_in_session,
)
from services.dependency_revision_terminal import (
    finalize_revised_checkpoint_turn_in_session,
)
from services.dependency_revision_two_step_terminal import (
    finalize_second_revised_turn_in_session,
)
from test_s25_07_gate_b3c3_result import store, _prepared
from test_s25_07_revision_continuation import _new_turn
from test_s25_07_gate_b3a_lineage import _b3b_host


def _invoke_a(Session, raw, binding, fence, settings, did, client=None):
    return invoke_first_revised_mcp_reference(
        Session, settings=settings, binding=binding,
        expected_raw=raw, initial_turn_raw=raw, fence=fence,
        dispatch_id=did,
        mcp_client=client if client is not None else StdioMCPClient(),
    )


def _begin_live_first(Session, cid):
    raw, binding, fence, cfg, did, _ = _prepared(
        Session, cid, completed=False)
    cfg = cfg.model_copy(update={
        "s25_07_revision_turn_terminal_enabled": True,
        "s25_07_revision_continuation_enabled": True,
        "s25_07_revision_registry_invocation_enabled": True,
    })
    return raw, binding, fence, cfg, did


def test_two_turn_both_real_tools_and_atomic_terminal_e2e(store):
    Session, cid = store
    initial, binding, first_fence, cfg, first_did = _begin_live_first(
        Session, cid)
    first_observation = _invoke_a(
        Session, initial, binding, first_fence, cfg, first_did)
    assert first_observation.get("ok") is True
    assert isinstance(first_observation.get("definition"), str)
    assert first_observation["definition"]
    with Session.begin() as db:
        checkpoint_revised_step_in_session(
            db, settings=cfg, binding=binding, expected_raw=initial,
            initial_turn_raw=initial, fence=first_fence,
            dispatch_id=first_did)
    with Session() as db:
        after_first = db.get(ConversationPlan, cid).snapshot
        receipt1 = json.loads(db.get(PlanToolDispatch, first_did).receipt_json)
        assert receipt1 == first_observation
    with Session.begin() as db:
        assert finalize_revised_checkpoint_turn_in_session(
            db, settings=cfg, owner_id=binding.owner_id,
            conversation_id=cid, expected_raw=after_first,
            turn_id=binding.turn_id, fence=first_fence) == "partial"

    second_turn, second_message = _new_turn(Session, binding, after_first)
    decision = _b3b_host(after_first, second_message)
    with Session.begin() as db:
        assert admit_revised_continuation_claim_in_session(
            db, settings=cfg, decision=decision,
            owner_id=binding.owner_id, conversation_id=cid,
            turn_id=second_turn, request_id="continuation-"+second_turn,
            expected_raw=after_first, initial_turn_raw=after_first) == 2
    second_fence = acquire_dependency_fence(
        Session, conversation_id=cid, turn_id=second_turn,
        expected_raw=after_first)
    assert second_fence.generation == first_fence.generation + 1
    with Session.begin() as db:
        second_did = begin_second_revised_dispatch_in_session(
            db, settings=cfg, decision=decision,
            owner_id=binding.owner_id, conversation_id=cid,
            turn_id=second_turn, request_id="continuation-"+second_turn,
            expected_raw=after_first, initial_turn_raw=after_first,
            fence=second_fence)
    with Session() as db:
        second_args = json.loads(db.get(PlanToolDispatch, second_did).arguments_json)
        assert second_args == {"text": first_observation["definition"]}
    second_observation = invoke_second_revised_registry_read(
        Session, settings=cfg, owner_id=binding.owner_id,
        conversation_id=cid, turn_id=second_turn,
        request_id="continuation-"+second_turn, expected_raw=after_first,
        initial_turn_raw=after_first, fence=second_fence,
        dispatch_id=second_did)
    assert second_observation == {
        "ok": True,
        "characters": len(first_observation["definition"]),
        "words": len(first_observation["definition"].split()),
    }
    with Session.begin() as db:
        checkpoint_second_revised_step_in_session(
            db, settings=cfg, owner_id=binding.owner_id,
            conversation_id=cid, turn_id=second_turn,
            request_id="continuation-"+second_turn,
            expected_raw=after_first, initial_turn_raw=after_first,
            fence=second_fence, dispatch_id=second_did)
    with Session() as db:
        finished = db.get(ConversationPlan, cid).snapshot
    with Session.begin() as db:
        assert finalize_second_revised_turn_in_session(
            db, settings=cfg, owner_id=binding.owner_id,
            conversation_id=cid, turn_id=second_turn,
            request_id="continuation-"+second_turn,
            expected_raw=finished, fence=second_fence) == "completed"
    with Session() as db:
        plan = json.loads(db.get(ConversationPlan, cid).snapshot)
        assert [s["status"] for s in plan["steps"]] == ["completed", "completed"]
        assert db.get(Turn, binding.turn_id).state == "partial"
        assert db.get(Turn, second_turn).state == "completed"
        assert db.get(PlanExecutionClaim, cid) is None
        assert db.get(DependencyWorkerFence, cid).state == "fenced"
        assert db.get(AgentTaskState, cid).state == "completed"
        assert db.get(AgentAttempt, first_did).status == "succeeded"
        assert db.get(AgentAttempt, second_did).status == "succeeded"
        assert [db.get(PlanToolDispatch, did).status
                for did in (first_did, second_did)] == [
                    "checkpointed", "checkpointed"]
        assert json.loads(db.get(
            PlanToolDispatch, second_did).arguments_json)["text"] == receipt1["definition"]
        assert db.get(Message, db.get(
            Turn, second_turn).assistant_message_id).status == "completed"


def test_real_mcp_first_step_invocation_is_once_and_checkpointable(store):
    Session, cid = store
    raw, binding, fence, cfg, did = _begin_live_first(Session, cid)
    first = _invoke_a(Session, raw, binding, fence, cfg, did)
    assert first["ok"] is True
    with pytest.raises(DependencyPlanError):
        _invoke_a(Session, raw, binding, fence, cfg, did)
    with Session() as db:
        row = db.get(PlanToolDispatch, did)
        assert row.status == "observed"
        assert json.loads(row.receipt_json) == first
        assert db.get(AgentAttempt, did) is None
    with Session.begin() as db:
        checkpoint_revised_step_in_session(
            db, settings=cfg, binding=binding,
            expected_raw=raw, initial_turn_raw=raw,
            fence=fence, dispatch_id=did)
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "checkpointed"


def test_real_mcp_no_call_when_gate_off_or_wrong_client(store):
    Session, cid = store
    raw, binding, fence, cfg, did = _begin_live_first(Session, cid)
    disabled = cfg.model_copy(update={
        "s25_07_revision_registry_invocation_enabled": False})
    with pytest.raises(DependencyPlanError):
        _invoke_a(Session, raw, binding, fence, disabled, did)
    with pytest.raises(DependencyPlanError):
        _invoke_a(Session, raw, binding, fence, cfg, did, client=object())
    with Session() as db:
        row = db.get(PlanToolDispatch, did)
        assert row.status == "dispatch_unknown"
        assert row.receipt_json is None


def test_real_mcp_crash_after_invoking_never_replays(store, monkeypatch):
    Session, cid = store
    raw, binding, fence, cfg, did = _begin_live_first(Session, cid)
    def worker_crash(*a, **kw):
        raise RuntimeError("after MCP invocation start")
    with monkeypatch.context() as patch:
        patch.setattr(READ_REGISTRY, "invoke", worker_crash)
        with pytest.raises(RuntimeError, match="after MCP"):
            _invoke_a(Session, raw, binding, fence, cfg, did)
    with Session() as db:
        row = db.get(PlanToolDispatch, did)
        assert row.status == "invoking"
        assert row.receipt_json is None
        assert db.get(ConversationPlan, cid).snapshot == raw
    with pytest.raises(DependencyPlanError):
        _invoke_a(Session, raw, binding, fence, cfg, did)


def test_real_mcp_catalog_failure_is_observed_and_not_retryable(store, monkeypatch):
    Session, cid = store
    raw, binding, fence, cfg, did = _begin_live_first(Session, cid)
    def catalog_error(*a, **kw):
        return {
            "ok": False, "error": "mcp_catalog_mismatch",
            "tool_name": "mcp_reference_lookup", "tool_version": "1.0.0"}
    with monkeypatch.context() as patch:
        patch.setattr(READ_REGISTRY, "invoke", catalog_error)
        out = _invoke_a(Session, raw, binding, fence, cfg, did)
    assert out == {"ok": False, "error": "mcp_catalog_mismatch"}
    with Session() as db:
        row = db.get(PlanToolDispatch, did)
        assert row.status == "observed_failure"
        assert json.loads(row.receipt_json) == out
    with pytest.raises(DependencyPlanError):
        _invoke_a(Session, raw, binding, fence, cfg, did)
    with pytest.raises(DependencyPlanError):
        with Session.begin() as db:
            checkpoint_revised_step_in_session(
                db, settings=cfg, binding=binding,
                expected_raw=raw, initial_turn_raw=raw,
                fence=fence, dispatch_id=did)
