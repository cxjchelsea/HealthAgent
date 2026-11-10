"""S25-06 Slice D transaction core: real PostgreSQL CAS/Claim physical tests.

Requires the existing isolated CI TEST_POSTGRES_URL. No tool invocation.
"""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest
from sqlalchemy import delete, select

from agent.dependency_plan_host import PreparedDependencyRead
from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, PlanExecutionClaim, PlanToolDispatch, Turn, database,
)
from services.dependency_execution import checkpoint_dependency_step_in_session
from services.evidence_store import arguments_digest, bounded_projection


@pytest.fixture
def physical():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requires isolated CI PostgreSQL")
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    did = str(uuid.uuid4())
    plan = {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "pending",
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
    raw = json.dumps(plan, ensure_ascii=False)
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="owner-physical"))
        db.flush()  # Real PostgreSQL FK: conversation before turn.
        db.add(Turn(id=tid, conversation_id=cid, request_id="req", state="running"))
        db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        db.flush()  # Claim and Dispatch both reference the existing Turn.
        db.add(PlanExecutionClaim(conversation_id=cid, turn_id=tid, snapshot=raw))
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=tid, step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            status="observed", arguments_json=json.dumps({"term": "RAG"}),
            receipt_json=json.dumps(receipt)))
    binding = PreparedDependencyRead(
        owner_id="owner-physical", conversation_id=cid,
        turn_id=tid, request_id="req", plan_revision=1,
        checkpoint_digest=sha256(raw.encode("utf-8")).hexdigest(),
        control="execute_first", step_id=1, tool_name="mcp_reference_lookup",
        tool_version="1.0.0", arguments={"term": "RAG"},
        arguments_digest=arguments_digest({"term": "RAG"}),
    )
    try:
        yield Session, cid, tid, did, raw, binding
    finally:
        with Session.begin() as db:
            db.execute(delete(AgentStateTransition).where(
                AgentStateTransition.conversation_id == cid))
            db.execute(delete(AgentAttempt).where(
                AgentAttempt.conversation_id == cid))
            db.execute(delete(AgentTaskState).where(
                AgentTaskState.conversation_id == cid))
            db.execute(delete(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            db.execute(delete(PlanExecutionClaim).where(
                PlanExecutionClaim.conversation_id == cid))
            db.execute(delete(ConversationPlan).where(
                ConversationPlan.conversation_id == cid))
            db.execute(delete(Turn).where(Turn.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()


def test_postgres_atomic_commit_and_exact_claim_roll(physical):
    Session, cid, tid, did, raw, binding = physical
    with Session.begin() as db:
        new = checkpoint_dependency_step_in_session(
            db, binding=binding, expected_raw=raw, dispatch_id=did)
        assert new["steps"][0]["status"] == "completed"
    with Session() as db:
        plan = db.get(ConversationPlan, cid)
        claim = db.get(PlanExecutionClaim, cid)
        dispatch = db.get(PlanToolDispatch, did)
        attempt = db.get(AgentAttempt, did)
        assert plan.snapshot == claim.snapshot
        assert dispatch.status == "checkpointed"
        assert attempt.status == "succeeded"
        assert db.get(AgentTaskState, cid).state == "partial"
        assert db.scalar(select(AgentStateTransition.id).where(
            AgentStateTransition.conversation_id == cid,
            AgentStateTransition.attempt_id == did)) is not None


def test_postgres_concurrent_finalizers_are_single_winner(physical):
    Session, cid, tid, did, raw, binding = physical

    def attempt():
        try:
            with Session.begin() as db:
                checkpoint_dependency_step_in_session(
                    db, binding=binding, expected_raw=raw, dispatch_id=did)
            return "committed"
        except DependencyPlanError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["committed", "dependency_execution_claim_conflict"]
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "checkpointed"
        assert db.query(AgentAttempt).filter_by(conversation_id=cid).count() == 1
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid, step_id=1).count() == 1
        assert db.get(ConversationPlan, cid).snapshot == db.get(
            PlanExecutionClaim, cid).snapshot
