"""S25-06: PostgreSQL real process-death boundaries for new dependency plans.

The child uses os._exit, bypassing Python context-manager teardown. A second
process reads the committed rows. Uncertain outcomes are NEVER replayed.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, PlanExecutionClaim, PlanToolDispatch,
    PrimaryRouteEvidence, Turn, database,
)
from services.evidence_store import arguments_digest
from services.primary_route_evidence import snapshot_digest


_CHILD = r"""
import hashlib
import json
import os
import sys

from agent.config import Settings
from agent.dependency_plan_host import bind_dependency_plan_read
from agent.primary_host_admission_v1 import PrimaryHostDecision
from services.db import database
from services.dependency_dispatch import begin_dependency_dispatch_in_session
from services.dependency_execution import checkpoint_dependency_step_in_session
from services.plan_dispatch import PlanDispatchJournal

url, cid, tid, initial, mode = sys.argv[1:]
_, Session = database(url)
settings = Settings(model_provider="fake", routing_profile="unified",
    primary_route_v1_canary_enabled=True, s25_06_dependency_plan_enabled=True)
decision = PrimaryHostDecision("allow", "primary_v1_plan_execution_candidate",
                              "plan_resume", "read_only", "resume")
binding = bind_dependency_plan_read(
    Session, settings=settings, decision=decision, owner_id="crash-owner",
    conversation_id=cid, turn_id=tid, request_id="req-" + tid,
    expected_raw=initial, control="resume", step_id=1)
with Session.begin() as db:
    did = begin_dependency_dispatch_in_session(
        db, binding=binding, expected_raw=initial, initial_turn_raw=initial)
if mode == "after_dispatch":
    os._exit(73)
observation = {
    "ok": True, "definition": "retrieval augmented generation",
    "source": "trusted-reference"}
PlanDispatchJournal(Session, cid, tid).record_receipt(did, observation)
if mode == "after_receipt":
    os._exit(73)
with Session.begin() as db:
    checkpoint_dependency_step_in_session(
        db, binding=binding, expected_raw=initial, dispatch_id=did)
    if mode == "inside_checkpoint":
        db.flush()
        os._exit(73)
os._exit(73)
"""


def _new_plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0", "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}}},
            {"id": 2, "goal": "stats", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0", "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ], "executions": {},
    }


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires physical TEST_POSTGRES_URL")
@pytest.mark.parametrize("point,expected_dispatch,checkpointed", [
    ("after_dispatch", "dispatch_unknown", False),
    ("after_receipt", "observed", False),
    ("inside_checkpoint", "observed", False),
    ("after_checkpoint", "checkpointed", True),
])
def test_real_postgres_process_exit_retains_exact_atomic_state(
        point, expected_dispatch, checkpointed):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    raw = json.dumps(_new_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="crash-owner"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid,
                    request_id="req-" + tid, state="running"))
        db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        db.flush()
        db.add(PlanExecutionClaim(conversation_id=cid, turn_id=tid, snapshot=raw))
        db.add(PrimaryRouteEvidence(
            turn_id=tid, conversation_id=cid, proposal_id=str(uuid.uuid4()),
            schema_version="primary-route-v1", route_kind="plan_resume",
            semantic_attempts=1, preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
    engine.dispose()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD, url, cid, tid, raw, point],
            cwd=str(Path(__file__).resolve().parents[1]),
            timeout=40, capture_output=True, text=True)
        assert proc.returncode == 73, (
            f"child stopped before {point}: {proc.stderr[-2500:]}")
        engine, Session = database(url)
        with Session() as db:
            plan_row = db.get(ConversationPlan, cid)
            claim = db.get(PlanExecutionClaim, cid)
            turn = db.get(Turn, tid)
            rows = list(db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)))
            attempts = list(db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)))
            transitions = list(db.scalars(select(AgentStateTransition).where(
                AgentStateTransition.conversation_id == cid)))
            assert turn.state == "running"  # real crash: no HTTP finally
            assert claim is not None and claim.snapshot == plan_row.snapshot
            assert len(rows) == 1 and rows[0].status == expected_dispatch
            assert len(attempts) == (1 if checkpointed else 0)
            assert len(transitions) == (2 if checkpointed else 0)
            plan = json.loads(plan_row.snapshot)
            assert plan["steps"][0]["status"] == (
                "completed" if checkpointed else "pending")
            assert len(plan["executions"]) == (1 if checkpointed else 0)
            if checkpointed:
                assert attempts[0].id == rows[0].id
                assert attempts[0].arguments_digest == arguments_digest({"term": "RAG"})
                assert db.get(AgentTaskState, cid).state == "partial"
            else:
                assert db.get(AgentTaskState, cid) is None
                assert rows[0].id not in plan["executions"]
    finally:
        engine, Session = database(url)
        with Session.begin() as db:
            db.execute(delete(AgentStateTransition).where(
                AgentStateTransition.conversation_id == cid))
            db.execute(delete(AgentAttempt).where(
                AgentAttempt.conversation_id == cid))
            db.execute(delete(AgentTaskState).where(
                AgentTaskState.conversation_id == cid))
            db.execute(delete(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            db.execute(delete(PrimaryRouteEvidence).where(
                PrimaryRouteEvidence.conversation_id == cid))
            db.execute(delete(PlanExecutionClaim).where(
                PlanExecutionClaim.conversation_id == cid))
            db.execute(delete(ConversationPlan).where(
                ConversationPlan.conversation_id == cid))
            db.execute(delete(Turn).where(Turn.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
