"""S25-07 Gate A: verified failure evidence must not become execution authority."""
import json

import pytest

from agent.config import Settings
from agent.dependency_plan_runner import run_dependency_read_scope
from agent.dependency_plan_schema import DependencyPlanError
from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.tool_registry import READ_REGISTRY
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, PlanExecutionClaim, PlanToolDispatch,
    PrimaryRouteEvidence, Turn, database,
)
from services.dependency_recovery import inspect_dependency_orphan
from services.migrate import migrate
from services.primary_route_evidence import snapshot_digest


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {"term": {
                 "kind": "literal", "value": "RAG", "source_quote": "RAG"}}},
            {"id": 2, "goal": "count", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {"text": {"kind": "observation_ref",
                                            "source_step_id": 1,
                                            "field": "definition"}}},
        ],
        "executions": {},
    }


@pytest.fixture
def seeded(tmp_path):
    url = "sqlite:///" + (tmp_path / "s25_07_gate_a.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    raw = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="owner"))
        db.add(Turn(id="tid", conversation_id="cid", request_id="req", state="running"))
        db.add(ConversationPlan(conversation_id="cid", snapshot=raw))
        db.add(PlanExecutionClaim(conversation_id="cid", turn_id="tid", snapshot=raw))
        db.add(PrimaryRouteEvidence(
            turn_id="tid", conversation_id="cid", proposal_id="p1",
            schema_version="primary-route-v1", route_kind="plan_resume",
            semantic_attempts=1, preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
    yield Session, raw
    engine.dispose()


def _execute(Session, raw, *, enabled=True):
    return run_dependency_read_scope(
        Session, settings=Settings(
            model_provider="fake", routing_profile="unified",
            primary_route_v1_canary_enabled=True,
            s25_06_dependency_plan_enabled=True,
            s25_07_failure_evidence_enabled=enabled),
        decision=PrimaryHostDecision(
            "allow", "primary_v1_plan_execution_candidate",
            "plan_resume", "read_only", "resume"),
        owner_id="owner", conversation_id="cid",
        turn_id="tid", request_id="req", initial_turn_raw=raw,
        control="resume")


def test_verified_negative_receipt_is_durable_but_step_stays_pending(seeded, monkeypatch):
    Session, raw = seeded
    calls = []
    def fail(name, *args, **kwargs):
        calls.append(name)
        return {"ok": False, "error": "trusted_adapter_failure"}
    monkeypatch.setattr(READ_REGISTRY, "invoke", fail)
    result = _execute(Session, raw)
    assert len(result) == 1
    assert result[0].status == "verified_failure"
    assert result[0].error == "dependency_tool_reported_failure"
    assert calls == ["mcp_reference_lookup"]
    with Session() as db:
        entry = db.query(PlanToolDispatch).one()
        assert entry.status == "failure_finalized"
        attempt = db.get(AgentAttempt, entry.id)
        assert attempt.status == "failed"
        assert attempt.error_code == "dependency_tool_reported_failure"
        assert json.loads(attempt.result_projection) == json.loads(entry.receipt_json)
        task = db.get(AgentTaskState, "cid")
        assert task.state == "blocked"
        assert task.plan_revision == 1
        steps = json.loads(task.step_states)
        assert [s["status"] for s in steps] == ["pending", "pending"]
        assert db.query(AgentStateTransition).filter_by(
            reason_code="verified_step_failed", attempt_id=entry.id).count() == 1
        assert db.get(ConversationPlan, "cid").snapshot == raw
        assert db.get(PlanExecutionClaim, "cid").snapshot == raw
    inspection = inspect_dependency_orphan(
        Session, owner_id="owner", conversation_id="cid", turn_id="tid")
    assert inspection.classification == "verified_failure_no_replay"
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        _execute(Session, raw)
    with Session() as db:
        assert db.query(PlanToolDispatch).count() == 1


def test_feature_off_retains_original_s25_06_failure_contract(seeded, monkeypatch):
    Session, raw = seeded
    monkeypatch.setattr(READ_REGISTRY, "invoke",
                        lambda *a, **k: {"ok": False, "error": "adapter_failed"})
    result = _execute(Session, raw, enabled=False)
    assert result[0].status == "blocked"
    assert result[0].error == "adapter_failed"
    with Session() as db:
        assert db.query(PlanToolDispatch).one().status == "observed_failure"
        assert db.query(AgentAttempt).count() == 0
        assert db.query(AgentTaskState).count() == 0


def test_unknown_outcome_remains_unreceipted_and_has_no_attempt(seeded, monkeypatch):
    Session, raw = seeded
    def crash(*args, **kwargs):
        raise RuntimeError("after durable dispatch, before receipt")
    monkeypatch.setattr(READ_REGISTRY, "invoke", crash)
    with pytest.raises(RuntimeError, match="before receipt"):
        _execute(Session, raw)
    with Session() as db:
        row = db.query(PlanToolDispatch).one()
        assert row.status == "dispatch_unknown"
        assert row.receipt_json is None
        assert db.query(AgentAttempt).count() == 0
        assert db.get(ConversationPlan, "cid").snapshot == raw


def test_failure_projection_rejects_tampered_arguments_atomically(seeded, monkeypatch):
    Session, raw = seeded
    import agent.dependency_plan_runner as runner
    original = runner.finalize_dependency_failure_in_session
    def tamper(db, **kwargs):
        entry = db.get(PlanToolDispatch, kwargs["dispatch_id"])
        entry.arguments_json = '{"term":"forged"}'
        # A failing transaction must roll back the adversarial change and
        # must not create a failed Attempt or task projection.
        return original(db, **kwargs)
    monkeypatch.setattr(runner, "finalize_dependency_failure_in_session", tamper)
    monkeypatch.setattr(READ_REGISTRY, "invoke",
                        lambda *a, **k: {"ok": False, "error": "adapter_failed"})
    with pytest.raises(DependencyPlanError, match="dependency_failure_receipt_conflict"):
        _execute(Session, raw)
    with Session() as db:
        row = db.query(PlanToolDispatch).one()
        assert row.status == "observed_failure"
        assert json.loads(row.arguments_json) == {"term": "RAG"}
        assert db.query(AgentAttempt).count() == 0
        assert db.query(AgentTaskState).count() == 0


def test_failure_is_not_a_completed_source_for_downstream(seeded, monkeypatch):
    Session, raw = seeded
    monkeypatch.setattr(READ_REGISTRY, "invoke",
                        lambda *a, **k: {"ok": False, "error": "adapter_failed"})
    _execute(Session, raw)
    with Session() as db:
        saved = json.loads(db.get(ConversationPlan, "cid").snapshot)
        assert saved["executions"] == {}
        assert saved["steps"][0]["status"] == "pending"
        assert saved["steps"][1]["status"] == "pending"
        assert db.query(PlanToolDispatch).count() == 1


def test_second_step_negative_receipt_preserves_verified_prefix(seeded, monkeypatch):
    Session, raw = seeded
    calls = []
    def observed(name, *args, **kwargs):
        calls.append(name)
        if name == "mcp_reference_lookup":
            return {"ok": True, "definition": "retrieval augmented generation",
                    "source": "trusted-reference"}
        return {"ok": False, "error": "text_stats_failed"}
    monkeypatch.setattr(READ_REGISTRY, "invoke", observed)
    outcomes = _execute(Session, raw)
    assert [x.status for x in outcomes] == ["checkpointed", "verified_failure"]
    assert calls == ["mcp_reference_lookup", "text_stats"]
    with Session() as db:
        data = json.loads(db.get(ConversationPlan, "cid").snapshot)
        assert [s["status"] for s in data["steps"]] == ["completed", "pending"]
        assert list(data["executions"]) == ["1"]
        dispatches = db.query(PlanToolDispatch).order_by(PlanToolDispatch.step_id).all()
        assert [x.status for x in dispatches] == [
            "checkpointed", "failure_finalized"]
        assert [db.get(AgentAttempt, d.id).status for d in dispatches] == [
            "succeeded", "failed"]
        state = db.get(AgentTaskState, "cid")
        assert state.state == "blocked"
        assert state.revision == 2
        assert state.plan_revision == 1
        assert json.loads(state.step_states)[0]["status"] == "completed"
        assert json.loads(state.step_states)[1]["status"] == "pending"
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        _execute(Session, raw)
    with Session() as db:
        assert db.query(PlanToolDispatch).count() == 2
