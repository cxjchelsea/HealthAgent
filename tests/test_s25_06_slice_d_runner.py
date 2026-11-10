"""S25-06 Slice D: real pinned local MCP -> text_stats single-Turn loop.

Tests use the existing local, fixed-catalog StdioMCPClient; no real LLM,
HTTP router or external MCP system is involved. The Host admission is a
scripted deterministic fixture with durable Primary evidence.
"""
import json

import pytest

from agent.config import Settings
from agent.dependency_plan_runner import (
    run_dependency_read_scope, run_one_dependency_read,
)
from agent.dependency_plan_schema import DependencyPlanError
from agent.mcp_stdio_client import StdioMCPClient
from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.tool_registry import ReadToolRuntime
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)
from services.migrate import migrate
from services.primary_route_evidence import snapshot_digest


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "reference", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "count", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ],
        "executions": {},
    }


@pytest.fixture
def seeded(tmp_path):
    url = "sqlite:///" + (tmp_path / "s25_06_d_runner.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    raw = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="owner"))
        db.add(Turn(id="tid", conversation_id="cid", request_id="req", state="running"))
        db.add(ConversationPlan(conversation_id="cid", snapshot=raw))
        db.add(PlanExecutionClaim(conversation_id="cid", turn_id="tid", snapshot=raw))
        db.add(PrimaryRouteEvidence(
            turn_id="tid", conversation_id="cid", proposal_id="proposal-1",
            schema_version="primary-route-v1", route_kind="plan_resume",
            semantic_attempts=1, preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
    yield Session, raw
    engine.dispose()


def _settings(**kw):
    return Settings(
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True, **kw)


def _decision():
    return PrimaryHostDecision(
        "allow", "primary_v1_plan_execution_candidate",
        "plan_resume", "read_only", "resume")


def _run(Session, raw, *, runtime=None, settings=None):
    return run_dependency_read_scope(
        Session, settings=settings or _settings(), decision=_decision(),
        owner_id="owner", conversation_id="cid", turn_id="tid",
        request_id="req", initial_turn_raw=raw, control="resume",
        runtime=runtime)


def test_two_real_local_read_tools_checkpoint_in_one_turn(seeded):
    Session, raw = seeded
    outcomes = _run(
        Session, raw,
        runtime=ReadToolRuntime(mcp_client=StdioMCPClient()))
    assert len(outcomes) == 2
    assert [x.status for x in outcomes] == ["checkpointed", "checkpointed"]
    with Session() as db:
        plan = json.loads(db.get(ConversationPlan, "cid").snapshot)
        assert all(s["status"] == "completed" for s in plan["steps"])
        assert len(plan["executions"]) == 2
        assert db.get(PlanExecutionClaim, "cid").snapshot == db.get(
            ConversationPlan, "cid").snapshot
        rows = db.query(PlanToolDispatch).order_by(PlanToolDispatch.step_id).all()
        assert [d.status for d in rows] == ["checkpointed", "checkpointed"]
        assert [d.tool_name for d in rows] == ["mcp_reference_lookup", "text_stats"]
        assert db.query(AgentAttempt).count() == 2
        source = json.loads(rows[0].receipt_json)["definition"]
        assert json.loads(rows[1].arguments_json) == {"text": source}
        downstream = json.loads(rows[1].receipt_json)
        assert downstream["characters"] == len(source)
        assert downstream["words"] == len(source.split())


def test_missing_runtime_blocked_receipt_is_not_retryable(seeded):
    Session, raw = seeded
    outcomes = _run(Session, raw)
    assert len(outcomes) == 1
    assert outcomes[0].status == "blocked"
    assert outcomes[0].error == "tool_runtime_unavailable"
    with Session() as db:
        entry = db.query(PlanToolDispatch).one()
        assert entry.status == "observed_failure"
        assert db.query(AgentAttempt).count() == 0
        assert db.get(ConversationPlan, "cid").snapshot == raw
    with pytest.raises(DependencyPlanError, match="dependency_execution_unreconciled"):
        _run(Session, raw, runtime=ReadToolRuntime(mcp_client=StdioMCPClient()))
    with Session() as db:
        assert db.query(PlanToolDispatch).count() == 1


def test_no_dispatch_without_durable_primary_evidence(seeded):
    Session, raw = seeded
    with Session.begin() as db:
        db.delete(db.get(PrimaryRouteEvidence, "tid"))
    with pytest.raises(DependencyPlanError, match="dependency_execution_claim_conflict"):
        _run(Session, raw, runtime=ReadToolRuntime(mcp_client=StdioMCPClient()))
    with Session() as db:
        assert db.query(PlanToolDispatch).count() == 0


def test_no_dispatch_with_primary_route_mismatch(seeded):
    Session, raw = seeded
    with Session.begin() as db:
        db.get(PrimaryRouteEvidence, "tid").route_kind = "plan_execute_first"
    with pytest.raises(DependencyPlanError, match="dependency_execution_claim_conflict"):
        _run(Session, raw, runtime=ReadToolRuntime(mcp_client=StdioMCPClient()))
    with Session() as db:
        assert db.query(PlanToolDispatch).count() == 0


def test_feature_off_prevents_dispatch(seeded):
    Session, raw = seeded
    disabled = Settings(model_provider="fake", routing_profile="unified",
                        primary_route_v1_canary_enabled=True)
    with pytest.raises(DependencyPlanError, match="dependency_host_disabled"):
        _run(Session, raw, settings=disabled)
    with Session() as db:
        assert db.query(PlanToolDispatch).count() == 0


def test_changed_initial_turn_digest_prevents_dispatch(seeded):
    Session, raw = seeded
    with pytest.raises(DependencyPlanError, match="dependency_execution_claim_conflict"):
        run_one_dependency_read(
            Session, settings=_settings(), decision=_decision(),
            owner_id="owner", conversation_id="cid",
            turn_id="tid", request_id="req", initial_turn_raw="stale-initial",
            current_raw=raw, control="resume", step_id=1,
            runtime=ReadToolRuntime(mcp_client=StdioMCPClient()))
    with Session() as db:
        assert db.query(PlanToolDispatch).count() == 0
