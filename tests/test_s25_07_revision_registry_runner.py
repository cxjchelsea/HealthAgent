"""S25-07 real registered text_stats invocation under durable v2 Dispatch.

The first Turn still uses controlled synthetic fixture evidence. The second
Turn now invokes the actual local read adapter exactly once per reservation.
"""
import json

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from agent.tool_registry import READ_REGISTRY
from services.db import AgentAttempt, ConversationPlan, PlanToolDispatch
from services.dependency_revision_registry_runner import (
    invoke_second_revised_registry_read,
)
from test_s25_07_revision_second import (
    store, _prepare, _begin, _checkpoint,
)


def _call(Session, first, cfg, tid, fence, raw, did):
    return invoke_second_revised_registry_read(
        Session, settings=cfg, owner_id=first.owner_id,
        conversation_id=first.conversation_id,
        turn_id=tid, request_id="continuation-"+tid,
        expected_raw=raw, initial_turn_raw=raw,
        fence=fence, dispatch_id=did,
    )


def test_real_registry_text_stats_once_then_checkpoint(store):
    Session, cid = store
    first, _, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    cfg = cfg.model_copy(update={
        "s25_07_revision_registry_invocation_enabled": True})
    with Session() as db:
        entry = db.get(PlanToolDispatch, did)
        args = json.loads(entry.arguments_json)
        assert entry.status == "dispatch_unknown"
    result = _call(Session, first, cfg, tid, fence, raw, did)
    assert result == {"ok": True, "characters": len(args["text"]),
                      "words": len(args["text"].split())}
    with Session() as db:
        entry = db.get(PlanToolDispatch, did)
        assert entry.status == "observed"
        assert json.loads(entry.receipt_json) == result
        assert db.get(AgentAttempt, did) is None
        assert db.get(ConversationPlan, cid).snapshot == raw
    with pytest.raises(DependencyPlanError):
        _call(Session, first, cfg, tid, fence, raw, did)
    completed = _checkpoint(Session, first, cfg, tid, fence, raw, did)
    assert completed["executions"]["2"]["dispatch_id"] == did


def test_real_registry_invocation_gate_off_does_not_consume_slot(store):
    Session, cid = store
    first, _, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    with pytest.raises(DependencyPlanError,
                       match="dependency_v2_registry_invocation_disabled"):
        _call(Session, first, cfg, tid, fence, raw, did)
    with Session() as db:
        entry = db.get(PlanToolDispatch, did)
        assert entry.status == "dispatch_unknown"
        assert entry.receipt_json is None


def test_registry_unknown_outcome_remains_blocked_and_never_auto_retried(
    store, monkeypatch,
):
    Session, cid = store
    first, _, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    cfg = cfg.model_copy(update={
        "s25_07_revision_registry_invocation_enabled": True})
    def simulate_crash(*args, **kwargs):
        raise RuntimeError("worker died after invocation began")
    with monkeypatch.context() as patcher:
        patcher.setattr(READ_REGISTRY, "invoke", simulate_crash)
        with pytest.raises(RuntimeError, match="worker died"):
            _call(Session, first, cfg, tid, fence, raw, did)
    with Session() as db:
        entry = db.get(PlanToolDispatch, did)
        assert entry.status == "invoking"
        assert entry.receipt_json is None
        assert db.get(AgentAttempt, did) is None
        assert db.get(ConversationPlan, cid).snapshot == raw
    # Even a now-healthy registry cannot claim that no earlier call happened.
    with pytest.raises(DependencyPlanError,
                       match="dependency_v2_registry_invocation_replayed"):
        _call(Session, first, cfg, tid, fence, raw, did)
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "invoking"


def test_registry_invalid_success_never_creates_usable_receipt(
    store, monkeypatch,
):
    Session, cid = store
    first, _, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    cfg = cfg.model_copy(update={
        "s25_07_revision_registry_invocation_enabled": True})
    with monkeypatch.context() as patcher:
        patcher.setattr(READ_REGISTRY, "invoke",
                        lambda *a, **k: {"ok": True, "tool_name": "text_stats",
                                        "tool_version": "1.0.0",
                                        "characters": 999, "words": 999})
        with pytest.raises(DependencyPlanError,
                           match="dependency_v2_registry_invalid_result"):
            _call(Session, first, cfg, tid, fence, raw, did)
    with Session() as db:
        row = db.get(PlanToolDispatch, did)
        assert row.status == "invoking"
        assert row.receipt_json is None
    with pytest.raises(DependencyPlanError):
        _call(Session, first, cfg, tid, fence, raw, did)


def test_registry_failure_receipt_is_terminal_not_retryable(store, monkeypatch):
    Session, cid = store
    first, _, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    cfg = cfg.model_copy(update={
        "s25_07_revision_registry_invocation_enabled": True})
    with monkeypatch.context() as patcher:
        patcher.setattr(READ_REGISTRY, "invoke",
                        lambda *a, **k: {"ok": False,
                                        "error": "tool_execution_error",
                                        "tool_name": "text_stats",
                                        "tool_version": "1.0.0"})
        assert _call(Session, first, cfg, tid, fence, raw, did) == {
            "ok": False, "error": "tool_execution_error"}
    with Session() as db:
        row = db.get(PlanToolDispatch, did)
        assert row.status == "observed_failure"
        assert json.loads(row.receipt_json)["ok"] is False
    with pytest.raises(DependencyPlanError):
        _call(Session, first, cfg, tid, fence, raw, did)
    with pytest.raises(DependencyPlanError):
        _checkpoint(Session, first, cfg, tid, fence, raw, did)
