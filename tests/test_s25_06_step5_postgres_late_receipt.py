"""Step 5 PostgreSQL late Receipt writer versus operator reconciliation.

Two independent database processes are involved. A delayed writer holds a
test-only copy of a generation token and starts *before* recovery, but attempts
its journal commit only after the original worker has been terminated and
recovery revokes that generation. This is a controlled stale-commit race,
not proof of arbitrary real-world worker-death attestation or external effects.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import delete

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, DependencyWorkerFence,
    PlanExecutionClaim, PlanRecoveryAudit, PlanToolDispatch,
    PrimaryRouteEvidence, Turn, database,
)
from services.dependency_fencing import acquire_dependency_fence
from services.dependency_recovery import (
    inspect_dependency_orphan, reconcile_dependency_orphan,
)
from services.primary_route_evidence import snapshot_digest

# A fake in-flight stale result is deliberately NOT presented as a real tool
# response. The only expected outcome is refusal BEFORE receipt persistence.
_LATE = r"""
import json
import os
from pathlib import Path
import sys
import time
from agent.dependency_plan_schema import DependencyPlanError
from services.dependency_fencing import DependencyFenceToken
from services.plan_dispatch import DispatchConflict, PlanDispatchJournal
from services.db import database

url, cid, tid, did, token_file, ready_file, go_file, result_file = sys.argv[1:]
secret = json.loads(Path(token_file).read_text(encoding="utf-8"))
fence = DependencyFenceToken(
    conversation_id=cid, turn_id=tid,
    generation=secret["generation"], secret=secret["secret"])
Path(ready_file).write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 25
while not Path(go_file).exists() and time.monotonic() < deadline:
    time.sleep(.025)
if not Path(go_file).exists():
    os._exit(70)
engine, Session = database(url)
journal = PlanDispatchJournal(Session, cid, tid)
try:
    journal.record_receipt(did, {
        "ok": True, "definition": "synthetic-untrusted-late-result",
        "source": "not-a-real-tool-result"}, fence=fence)
except (DependencyPlanError, DispatchConflict) as exc:
    code = str(exc)
    if code != "dependency_worker_fenced":
        Path(result_file).write_text("unexpected_denial", encoding="utf-8")
        os._exit(71)
    Path(result_file).write_text("fenced", encoding="utf-8")
    os._exit(0)
Path(result_file).write_text("observed", encoding="utf-8")
os._exit(0)
"""


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0", "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "statistics", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0", "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ],
        "executions": {},
    }


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires isolated real PostgreSQL")
@pytest.mark.parametrize("receipt_wins", [False, True])
def test_postgres_recovery_fences_late_receipt_from_independent_process(
        tmp_path, receipt_wins):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid, tid, did = (str(uuid.uuid4()) for _ in range(3))
    raw = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="step5-owner"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid,
                    request_id="step5-late", state="running"))
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
    fence = acquire_dependency_fence(
        Session, conversation_id=cid, turn_id=tid, expected_raw=raw)
    with Session.begin() as db:
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", status="dispatch_unknown",
            arguments_json='{"term":"RAG"}',
            worker_generation=fence.generation,
            worker_token_digest=db.get(
                DependencyWorkerFence, cid).token_digest))
    # Token file is private to this CI tmpdir, not included in reports.
    token_file = tmp_path / "test-only-fence-token.json"
    token_file.write_text(json.dumps({
        "generation": fence.generation, "secret": fence.secret}),
        encoding="utf-8")
    ready, go, result = (tmp_path / name for name in (
        "late-ready", "late-go", "late-result"))
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _LATE, url, cid, tid, did,
             str(token_file), str(ready), str(go), str(result)],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            assert proc.poll() is None, "stale worker exited prematurely"
            time.sleep(.025)
        assert ready.exists(), "stale worker did not reach wait gate"
        if receipt_wins:
            # The deterministic earlier-Receipt order is *not* a zombie:
            # the worker has completed the journal transaction before the
            # operator takes a fresh evidence snapshot. Receipt remains
            # observed, never silently checkpointed as successful.
            go.touch()
            _, stderr = proc.communicate(timeout=20)
            assert proc.returncode == 0, stderr[-2500:]
            assert result.read_text(encoding="utf-8") == "observed"
        evidence = inspect_dependency_orphan(
            Session, owner_id="step5-owner", conversation_id=cid,
            turn_id=tid)
        assert evidence.classification == "uncertain_no_replay"
        # The external verifiers are test stubs, never production IAM.
        # In receipt-first mode worker exit is independently observed; in
        # recovery-first mode the delayed process is ONLY a synthetic stale
        # token holder, not the original worker claimed to be dead.
        reconcile_dependency_orphan(
            Session, owner_id="step5-owner", conversation_id=cid,
            turn_id=tid, operator_id="test-operator",
            expected_plan_sha256=evidence.plan_sha256,
            expected_evidence_sha256=evidence.evidence_sha256,
            operator_authorized=True, worker_terminated_confirmed=True,
            operator_access_verifier=lambda *_: True,
            worker_termination_verifier=lambda *_: (
                not receipt_wins or proc.poll() == 0),
        )
        if not receipt_wins:
            go.touch()
            _, stderr = proc.communicate(timeout=20)
            assert proc.returncode == 0, stderr[-2500:]
            assert result.read_text(encoding="utf-8") == "fenced"
        with Session() as db:
            dispatch = db.get(PlanToolDispatch, did)
            assert dispatch.status == (
                "observed" if receipt_wins else "dispatch_unknown")
            if receipt_wins:
                assert dispatch.receipt_json is not None
            else:
                assert dispatch.receipt_json is None
            assert db.query(AgentAttempt).filter_by(
                conversation_id=cid).count() == 0
            assert db.get(ConversationPlan, cid).snapshot == raw
            assert db.get(Turn, tid).state == "partial"
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.get(DependencyWorkerFence, cid).state == "fenced"
    finally:
        go.touch()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
        with Session.begin() as db:
            for model in (
                PlanRecoveryAudit, AgentAttempt, PlanToolDispatch,
                PrimaryRouteEvidence, PlanExecutionClaim,
                DependencyWorkerFence, ConversationPlan, Turn,
            ):
                db.execute(delete(model).where(model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
