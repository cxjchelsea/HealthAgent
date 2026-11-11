"""S25-06 Step 5: abruptly terminated new-format worker on real PostgreSQL.

The spawned process acquires the exact new-format worker Fence and writes the
Host-validated pre-call Dispatch, optionally invokes the trusted local MCP
read adapter, then exits via os._exit without a Receipt.
A separate parent process inspects and reconciles it without ever retrying the
tool. Only synthetic, independently checked operator verifiers are used.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete

from services.db import (
    AgentAttempt, Conversation, ConversationPlan, DependencyWorkerFence,
    PlanExecutionClaim, PlanRecoveryAudit, PlanToolDispatch, PrimaryRouteEvidence,
    Turn, database,
)
from services.dependency_recovery import (
    inspect_dependency_orphan, reconcile_dependency_orphan,
)
from services.primary_route_evidence import snapshot_digest

_CHILD = r"""
import os
import sys
from pathlib import Path
from hashlib import sha256
from agent.dependency_plan_host import PreparedDependencyRead
from services.db import database
from services.dependency_fencing import acquire_dependency_fence
from services.dependency_dispatch import begin_dependency_dispatch_in_session
from services.evidence_store import arguments_digest

url, cid, tid, raw, call_tool, marker_path = sys.argv[1:]
engine, Session = database(url)
fence = acquire_dependency_fence(
    Session, conversation_id=cid, turn_id=tid, expected_raw=raw)
arguments = {"term": "RAG"}
binding = PreparedDependencyRead(
    owner_id="physical-owner", conversation_id=cid,
    turn_id=tid, request_id="step5-abrupt-worker",
    plan_revision=1, checkpoint_digest=sha256(raw.encode("utf-8")).hexdigest(),
    control="resume", step_id=1, tool_name="mcp_reference_lookup",
    tool_version="1.0.0", arguments=arguments,
    arguments_digest=arguments_digest(arguments),
)
with Session.begin() as db:
    begin_dependency_dispatch_in_session(
        db, binding=binding, expected_raw=raw,
        initial_turn_raw=raw, fence=fence)
if call_tool == "yes":
    from agent.tool_registry import READ_REGISTRY, ReadToolRuntime
    from agent.mcp_stdio_client import StdioMCPClient
    observation = READ_REGISTRY.invoke(
        "mcp_reference_lookup", {"term": "RAG"},
        granted_names=("mcp_reference_lookup",),
        exposed_names=("mcp_reference_lookup",),
        expected_version="1.0.0",
        runtime=ReadToolRuntime(mcp_client=StdioMCPClient()))
    assert observation.get("ok") is True, observation.get("error")
    assert isinstance(observation.get("definition"), str)
    # File is strictly a test-side invocation marker. It does NOT imply
    # that a Receipt was committed in PostgreSQL.
    Path(marker_path).write_text("tool-returned", encoding="utf-8")
# Crash AFTER the PostgreSQL pre-call transaction, either before invoking
# the tool or after its real read result but before persisting the Receipt.
os._exit(73)
"""


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {
                "id": 1, "goal": "query", "depends_on": [],
                "status": "pending", "expected_tool": "mcp_reference_lookup",
                "tool_version": "1.0.0",
                "argument_bindings": {
                    "term": {"kind": "literal", "value": "RAG",
                             "source_quote": "RAG"},
                },
            },
            {
                "id": 2, "goal": "statistics", "depends_on": [1],
                "status": "pending", "expected_tool": "text_stats",
                "tool_version": "1.0.0",
                "argument_bindings": {
                    "text": {"kind": "observation_ref",
                             "source_step_id": 1, "field": "definition"},
                },
            },
        ],
        "executions": {},
    }


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires isolated real PostgreSQL")
@pytest.mark.parametrize("crash_after_tool", [False, True])
def test_postgres_abrupt_new_format_worker_recovery_never_replays(
        tmp_path, crash_after_tool):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid, tid = str(uuid.uuid4()), str(uuid.uuid4())
    raw = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="physical-owner"))
        db.flush()
        db.add(Turn(
            id=tid, conversation_id=cid, request_id="step5-abrupt-worker",
            state="running"))
        db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        db.flush()
        db.add(PlanExecutionClaim(
            conversation_id=cid, turn_id=tid, snapshot=raw))
        db.add(PrimaryRouteEvidence(
            turn_id=tid, conversation_id=cid,
            proposal_id=str(uuid.uuid4()),
            schema_version="primary-route-v1", route_kind="plan_resume",
            semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
    engine.dispose()
    child = None
    tool_marker = tmp_path / "tool-returned.marker"
    try:
        child = subprocess.run(
            [sys.executable, "-c", _CHILD, url, cid, tid, raw,
             "yes" if crash_after_tool else "no", str(tool_marker)],
            cwd=str(Path(__file__).resolve().parents[1]), timeout=35,
            capture_output=True, text=True,
        )
        assert child.returncode == 73, child.stderr[-2500:]
        assert tool_marker.exists() is crash_after_tool
        engine, Session = database(url)
        with Session() as db:
            dispatches = db.query(PlanToolDispatch).filter_by(
                conversation_id=cid).all()
            assert len(dispatches) == 1
            assert dispatches[0].status == "dispatch_unknown"
            assert dispatches[0].receipt_json is None
            assert dispatches[0].tool_version == "1.0.0"
            assert db.query(AgentAttempt).filter_by(
                conversation_id=cid).count() == 0
            assert db.get(DependencyWorkerFence, cid).state == "active"
            assert db.get(ConversationPlan, cid).snapshot == raw
            assert db.get(Turn, tid).state == "running"
        checked = inspect_dependency_orphan(
            Session, owner_id="physical-owner", conversation_id=cid,
            turn_id=tid)
        assert checked.classification == "uncertain_no_replay"
        reconcile_dependency_orphan(
            Session, owner_id="physical-owner", conversation_id=cid,
            turn_id=tid, operator_id="synthetic-operator",
            expected_plan_sha256=checked.plan_sha256,
            expected_evidence_sha256=checked.evidence_sha256,
            operator_authorized=True, worker_terminated_confirmed=True,
            operator_access_verifier=lambda op, owner, conversation, turn: (
                op == "synthetic-operator" and owner == "physical-owner"
                and conversation == cid and turn == tid),
            # Actual subprocess termination is observed by the parent. This
            # verifier is test-specific, not operator IAM for production.
            worker_termination_verifier=lambda conversation, turn: (
                conversation == cid and turn == tid
                and child.returncode == 73),
        )
        with Session() as db:
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.get(DependencyWorkerFence, cid).state == "fenced"
            assert db.get(Turn, tid).failure_code == (
                "dependency_reconciliation_required")
            assert db.get(ConversationPlan, cid).snapshot == raw
            entries = db.query(PlanToolDispatch).filter_by(
                conversation_id=cid).all()
            assert len(entries) == 1
            assert entries[0].status == "dispatch_unknown"
            assert db.query(AgentAttempt).filter_by(
                conversation_id=cid).count() == 0
            assert db.query(PlanRecoveryAudit).filter_by(
                conversation_id=cid).count() == 1
    finally:
        engine, Session = database(url)
        with Session.begin() as db:
            for model in (
                PlanRecoveryAudit, AgentAttempt, PlanToolDispatch,
                PrimaryRouteEvidence, PlanExecutionClaim,
                DependencyWorkerFence, ConversationPlan, Turn,
            ):
                db.execute(delete(model).where(model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
