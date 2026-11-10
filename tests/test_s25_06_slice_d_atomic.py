"""S25-06 Slice D checkpoint transaction physical tests (SQLite, isolated).

The observed receipt is deliberately seeded, not a simulated Host permission;
these tests exercise the atomic finalizer and rollback, NOT live tool routing.
"""
import hashlib
import json

import pytest

from agent.dependency_plan_host import PreparedDependencyRead
from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, PlanExecutionClaim, PlanToolDispatch, Turn, database,
)
from services.dependency_execution import checkpoint_dependency_step_in_session
from services.evidence_store import arguments_digest, bounded_projection
from services.migrate import migrate


def _plan(completed=False):
    a = {"term": "RAG"}
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "completed" if completed else "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {"term": {"kind": "literal", "value": "RAG",
                                              "source_quote": "RAG"}}},
            {"id": 2, "goal": "count", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {"text": {"kind": "observation_ref",
                                              "source_step_id": 1,
                                              "field": "definition"}}},
        ],
        "executions": ({
            "1": {"dispatch_id": "d1", "step_id": 1, "plan_revision": 1,
                  "tool_name": "mcp_reference_lookup", "tool_version": "1.0.0",
                  "resolved_arguments_digest": arguments_digest(a),
                  "source_evidence_refs": []}
        } if completed else {}),
    }


def _binding(raw, *, step_id=1, source=None):
    tool = "mcp_reference_lookup" if step_id == 1 else "text_stats"
    args = {"term": "RAG"} if step_id == 1 else {"text": "retrieval augmented generation"}
    return PreparedDependencyRead(
        owner_id="owner", conversation_id="cid",
        turn_id="turn", request_id="req", plan_revision=1,
        checkpoint_digest=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        control="execute_first" if step_id == 1 else "resume",
        step_id=step_id, tool_name=tool, tool_version="1.0.0",
        arguments=args, arguments_digest=arguments_digest(args),
        source_dispatch_id=source,
    )


@pytest.fixture
def case(tmp_path):
    url = "sqlite:///" + (tmp_path / "step.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    raw = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="owner"))
        db.add(ConversationPlan(conversation_id="cid", snapshot=raw))
        db.add(Turn(id="turn", conversation_id="cid", request_id="req", state="running"))
        db.add(PlanExecutionClaim(conversation_id="cid", turn_id="turn", snapshot=raw))
        receipt = bounded_projection("mcp_reference_lookup", {
            "ok": True, "definition": "retrieval augmented generation",
            "source": "trusted-reference"})
        db.add(PlanToolDispatch(
            id="d1", conversation_id="cid", turn_id="turn", step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            status="observed", arguments_json=json.dumps({"term": "RAG"}),
            receipt_json=json.dumps(receipt)))
    yield Session
    engine.dispose()


def _raw(Session):
    with Session() as db:
        return db.get(ConversationPlan, "cid").snapshot


def _finish(Session, binding, raw, dispatch_id):
    with Session.begin() as db:
        return checkpoint_dependency_step_in_session(
            db, binding=binding, expected_raw=raw, dispatch_id=dispatch_id)


def _reject(Session, binding, raw, dispatch_id, code):
    with pytest.raises(DependencyPlanError) as err:
        _finish(Session, binding, raw, dispatch_id)
    assert err.value.code == code


def _state(Session):
    with Session() as db:
        return {
            "plan": db.get(ConversationPlan, "cid").snapshot,
            "claim": db.get(PlanExecutionClaim, "cid").snapshot,
            "receipt": db.get(PlanToolDispatch, "d1").status,
            "attempts": db.query(AgentAttempt).count(),
            "tasks": db.query(AgentTaskState).count(),
            "transitions": db.query(AgentStateTransition).count(),
        }


def test_step_commit_atomically_updates_plan_claim_receipt_attempt_task(case):
    raw = _raw(case)
    new = _finish(case, _binding(raw), raw, "d1")
    after = _state(case)
    assert new["steps"][0]["status"] == "completed"
    assert new["executions"]["1"]["dispatch_id"] == "d1"
    assert after["plan"] == after["claim"]
    assert after["plan"] != raw
    assert after["receipt"] == "checkpointed"
    assert after["attempts"] == after["tasks"] == 1
    assert after["transitions"] == 2
    with case() as db:
        a = db.get(AgentAttempt, "d1")
        assert a.status == "succeeded"
        assert a.arguments_digest == arguments_digest({"term": "RAG"})
        transitions = db.query(AgentStateTransition).filter_by(
            entity_type="step").all()
        assert transitions[0].attempt_id == "d1"
        assert transitions[0].reason_code == "verified_step_completed"


def test_stale_repeated_commit_does_not_replay(case):
    raw = _raw(case)
    b = _binding(raw)
    _finish(case, b, raw, "d1")
    state = _state(case)
    _reject(case, b, raw, "d1", "dependency_execution_claim_conflict")
    assert _state(case) == state


@pytest.mark.parametrize("mutation,error", [
    ("receipt_unknown", "dependency_execution_receipt_conflict"),
    ("receipt_failure", "dependency_untrusted_observation"),
    ("wrong_args", "dependency_execution_receipt_conflict"),
    ("wrong_version", "dependency_execution_receipt_conflict"),
    ("wrong_turn", "dependency_execution_receipt_conflict"),
    ("stale_claim", "dependency_execution_claim_conflict"),
    ("terminal_turn", "dependency_execution_claim_conflict"),
])
def test_failure_rolls_back_all_evidence(case, mutation, error):
    raw = _raw(case)
    with case.begin() as db:
        d = db.get(PlanToolDispatch, "d1")
        if mutation == "receipt_unknown":
            d.status = "dispatch_unknown"
        if mutation == "receipt_failure":
            d.receipt_json = '{"ok":false}'
        if mutation == "wrong_args":
            d.arguments_json = '{"term":"wrong"}'
        if mutation == "wrong_version":
            d.tool_version = "2.0.0"
        if mutation == "wrong_turn":
            d.turn_id = "other"
        if mutation == "stale_claim":
            db.get(PlanExecutionClaim, "cid").snapshot = "stale"
        if mutation == "terminal_turn":
            db.get(Turn, "turn").state = "completed"
    before = _state(case)
    _reject(case, _binding(raw), raw, "d1", error)
    assert _state(case) == before


def test_failed_projection_rolls_back_cas_claim_and_receipt(case):
    raw = _raw(case)
    with case.begin() as db:
        db.add(AgentAttempt(
            id="d1", conversation_id="cid", turn_id="turn", run_id="turn",
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_digest=arguments_digest({"term": "RAG"}),
            status="succeeded"))
    before = _state(case)
    _reject(case, _binding(raw), raw, "d1", "dependency_execution_duplicate_attempt")
    assert _state(case) == before


def test_explicit_rollback_after_checkpoint_rolls_everything_back(case):
    raw = _raw(case)
    with pytest.raises(RuntimeError, match="forced_after_checkpoint"):
        with case.begin() as db:
            checkpoint_dependency_step_in_session(
                db, binding=_binding(raw), expected_raw=raw, dispatch_id="d1")
            raise RuntimeError("forced_after_checkpoint")
    assert _state(case) == {
        "plan": raw, "claim": raw, "receipt": "observed",
        "attempts": 0, "tasks": 0, "transitions": 0,
    }


def test_two_step_serial_checkpoint(case):
    raw = _raw(case)
    _finish(case, _binding(raw), raw, "d1")
    current = _raw(case)
    data = bounded_projection("text_stats", {
        "ok": True, "characters": 30, "words": 3})
    with case.begin() as db:
        db.add(PlanToolDispatch(
            id="d2", conversation_id="cid", turn_id="turn", step_id=2,
            tool_name="text_stats", tool_version="1.0.0", status="observed",
            arguments_json=json.dumps({"text": "retrieval augmented generation"}),
            receipt_json=json.dumps(data)))
    next_plan = _finish(case, _binding(current, step_id=2, source="d1"), current, "d2")
    assert all(s["status"] == "completed" for s in next_plan["steps"])
    with case() as db:
        assert db.get(AgentTaskState, "cid").state == "completed"
        assert db.get(PlanToolDispatch, "d2").status == "checkpointed"
        assert db.query(AgentAttempt).count() == 2
        assert db.get(PlanExecutionClaim, "cid").snapshot == db.get(ConversationPlan, "cid").snapshot


def test_second_step_rejects_bad_source(case):
    raw = _raw(case)
    _finish(case, _binding(raw), raw, "d1")
    current = _raw(case)
    with case.begin() as db:
        db.add(PlanToolDispatch(
            id="d2", conversation_id="cid", turn_id="turn", step_id=2,
            tool_name="text_stats", tool_version="1.0.0", status="observed",
            arguments_json=json.dumps({"text": "retrieval augmented generation"}),
            receipt_json='{"ok":true,"characters":30,"words":3}'))
    _reject(case, _binding(current, step_id=2, source="unknown"),
            current, "d2", "dependency_untrusted_observation")
    with case() as db:
        assert db.get(PlanToolDispatch, "d2").status == "observed"
        assert db.get(AgentAttempt, "d2") is None


def test_second_step_rejects_source_digest_tampering(case):
    raw = _raw(case)
    _finish(case, _binding(raw), raw, "d1")
    with case.begin() as db:
        row = db.get(ConversationPlan, "cid")
        checkpoint = json.loads(row.snapshot)
        checkpoint["executions"]["1"]["resolved_arguments_digest"] = "a" * 64
        changed = json.dumps(checkpoint, ensure_ascii=False)
        row.snapshot = changed
        db.get(PlanExecutionClaim, "cid").snapshot = changed
        db.add(PlanToolDispatch(
            id="d2", conversation_id="cid", turn_id="turn", step_id=2,
            tool_name="text_stats", tool_version="1.0.0", status="observed",
            arguments_json=json.dumps({"text": "retrieval augmented generation"}),
            receipt_json='{"ok":true,"characters":30,"words":3}'))
    changed_raw = _raw(case)
    _reject(case, _binding(changed_raw, step_id=2, source="d1"),
            changed_raw, "d2", "dependency_untrusted_observation")


def test_second_step_rejects_incorrect_tool_count(case):
    raw = _raw(case)
    _finish(case, _binding(raw), raw, "d1")
    current = _raw(case)
    with case.begin() as db:
        db.add(PlanToolDispatch(
            id="d2", conversation_id="cid", turn_id="turn", step_id=2,
            tool_name="text_stats", tool_version="1.0.0", status="observed",
            arguments_json=json.dumps({"text": "retrieval augmented generation"}),
            receipt_json='{"ok":true,"characters":999,"words":3}'))
    _reject(case, _binding(current, step_id=2, source="d1"),
            current, "d2", "dependency_untrusted_observation")


def test_second_step_rejects_unrelated_task_projection(case):
    raw = _raw(case)
    _finish(case, _binding(raw), raw, "d1")
    current = _raw(case)
    with case.begin() as db:
        task = db.get(AgentTaskState, "cid")
        task.step_states = json.dumps([{"id": 99, "status": "completed",
                                        "evidence_tool": "create_task"}])
        db.add(PlanToolDispatch(
            id="d2", conversation_id="cid", turn_id="turn", step_id=2,
            tool_name="text_stats", tool_version="1.0.0", status="observed",
            arguments_json=json.dumps({"text": "retrieval augmented generation"}),
            receipt_json='{"ok":true,"characters":30,"words":3}'))
    with pytest.raises(DependencyPlanError, match="dependency_task_projection_conflict"):
        _finish(case, _binding(current, step_id=2, source="d1"), current, "d2")
    with case() as db:
        assert db.get(ConversationPlan, "cid").snapshot == current
        assert db.get(PlanExecutionClaim, "cid").snapshot == current
        assert db.get(PlanToolDispatch, "d2").status == "observed"
        assert db.get(AgentAttempt, "d2") is None
