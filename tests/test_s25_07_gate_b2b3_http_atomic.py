"""Gate B2b3: exact approved revision digest and official HTTP atomicity."""
import copy
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.dependency_revision_candidate import compile_revision_candidate
from agent.dependency_plan_schema import DependencyPlanError
from apps.api.main import create_app
from services.db import (
    AgentStateTransition, AgentTaskState, ConversationPlan, DependencyRevisionAnchor, Message,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)
from services.dependency_revision_fingerprint import revision_fingerprint
from agent.primary_route_v1 import RouteSignal, bind_route_signal

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
                 "kind": "observation_ref", "source_step_id": 1,
                 "field": "definition"}}},
        ], "executions": {},
    }


def raw_parent():
    return json.dumps(parent(), ensure_ascii=False)


def bind(*, raw):
    pending = copy.deepcopy(parent()["steps"])
    pending[1]["goal"] = GOAL
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "plan_revise", "quote": MESSAGE,
        "execution_scope": "none", "effect_intent": "mutation_requested",
        "step_number": 2, "dependency_revision_steps": pending,
        "revision_old_argument": None, "revision_new_argument": None,
    }), MESSAGE, saved_plan_present=True, pending_action=None,
        saved_step_count=2, dependency_revision_enabled=True,
        saved_plan_raw=raw)


@pytest.fixture(params=["sqlite", "postgres"])
def sandbox(tmp_path, request):
    if request.param == "postgres":
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("requires TEST_POSTGRES_URL")
    else:
        url = f"sqlite:///{tmp_path / 'revision-http.db'}"
    cfg = Settings(
        database_url=url, model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
        s25_07_dependency_revision_proposal_enabled=True)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(url)
        raw = raw_parent()
        with Session.begin() as db:
            db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        try:
            yield client, Session, cid, raw
        finally:
            # Only this conversation's rows; preserve other parallel PG tests.
            from sqlalchemy import delete
            from services.db import (
                Conversation, AgentAttempt, PlanExecutionClaim,
                DependencyWorkerFence,
            )
            with Session.begin() as db:
                for model in (
                    DependencyRevisionAnchor, AgentStateTransition, AgentTaskState, AgentAttempt,
                    PlanToolDispatch, PrimaryRouteEvidence,
                    PlanExecutionClaim, DependencyWorkerFence,
                    ConversationPlan, Turn, Message):
                    db.execute(delete(model).where(model.conversation_id == cid))
                db.execute(delete(Conversation).where(Conversation.id == cid))


def _route(monkeypatch):
    async def one(*args, **kwargs):
        return bind(raw=kwargs["saved_plan_raw"])
    monkeypatch.setattr("apps.api.main.propose_primary_route", one)


def _request(client, cid, key=None):
    return client.post(f"/conversations/{cid}/agent/messages", json={
        "content": MESSAGE, "client_request_id": key or str(uuid.uuid4())})


def test_exact_host_fingerprint_is_committed_and_old_executor_stays_off(
        sandbox, monkeypatch):
    client, Session, cid, raw = sandbox
    _route(monkeypatch)
    result = _request(client, cid)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["termination_reason"] == "dependency_revision_saved_pending_execution"
    assert data["plan_revision"] == 2
    with Session() as db:
        evidence = db.get(PrimaryRouteEvidence, data["turn_id"])
        saved = json.loads(db.get(ConversationPlan, cid).snapshot)
        assert evidence.approved_revision_digest == revision_fingerprint(saved)
        assert db.get(DependencyRevisionAnchor, cid).approved_digest == evidence.approved_revision_digest
        assert saved["plan_executor_version"] == "dependency-plan-read-replan-v1"
        assert db.get(AgentTaskState, cid).plan_revision == 2
        assert db.query(PlanToolDispatch).filter_by(conversation_id=cid).count() == 0
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid,
            reason_code="dependency_plan_revision_saved").count() == 1
        assert db.get(Turn, data["turn_id"]).state == "partial"
    replay = _request(client, cid, data["turn_id"])
    # A different client request can still be refused by Host because the
    # saved plan is now revision 2, not a reason to grant B3 execution.
    assert replay.status_code == 200
    assert replay.json()["termination_reason"] != "dependency_revision_saved_pending_execution"


def test_swapped_valid_user_grounded_candidate_denied_atomically(
        sandbox, monkeypatch):
    client, Session, cid, raw = sandbox
    _route(monkeypatch)
    from services.dependency_revision_commit import (
        commit_dependency_revision_in_session as real_commit,
    )

    def swapped(db, **kwargs):
        different = copy.deepcopy(parent()["steps"])
        different[1]["goal"] = "第二步的说明"
        alternate = compile_revision_candidate(
            expected_raw=kwargs["expected_raw"],
            replacement_pending=different, current_user_message=MESSAGE)
        kwargs["proposal"] = alternate
        return real_commit(db, **kwargs)

    monkeypatch.setattr("apps.api.main.commit_dependency_revision_in_session", swapped)
    resp = _request(client, cid)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["termination_reason"] == "dependency_revision_approval_mismatch"
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(AgentTaskState, cid) is None
        assert db.query(AgentStateTransition).filter_by(conversation_id=cid).count() == 0
        evidence = db.get(PrimaryRouteEvidence, data["turn_id"])
        assert evidence.disposition == "allow"
        assert evidence.approved_revision_digest is not None
        assert db.get(Turn, data["turn_id"]).state == "partial"


def test_concurrent_plan_change_after_host_admission_refuses_stale_cas(
        sandbox, monkeypatch):
    client, Session, cid, raw = sandbox
    _route(monkeypatch)
    from services.primary_route_evidence import record_primary_admission as record

    def race(**kwargs):
        saved_id = record(**kwargs)
        if kwargs["decision"] is not None and kwargs["decision"].reason == "dependency_revision_candidate":
            other = json.loads(raw)
            other["steps"][1]["goal"] = "concurrent writer changed plan"
            with Session.begin() as db:
                db.get(ConversationPlan, cid).snapshot = json.dumps(
                    other, ensure_ascii=False)
        return saved_id

    monkeypatch.setattr("apps.api.main.record_primary_admission", race)
    resp = _request(client, cid)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["termination_reason"] == "dependency_revision_snapshot_conflict"
    with Session() as db:
        assert json.loads(db.get(ConversationPlan, cid).snapshot)["steps"][1][
            "goal"] == "concurrent writer changed plan"
        assert db.get(AgentTaskState, cid) is None
        assert db.query(AgentStateTransition).filter_by(conversation_id=cid).count() == 0


def test_fingerprint_is_order_stable_but_candidate_sensitive():
    candidate = {"revision": 2, "steps": [{"goal": "A"}]}
    same = {"steps": [{"goal": "A"}], "revision": 2}
    changed = {"revision": 2, "steps": [{"goal": "B"}]}
    assert revision_fingerprint(candidate) == revision_fingerprint(same)
    assert revision_fingerprint(candidate) != revision_fingerprint(changed)


def test_unconfirmed_terminal_failure_never_claims_saved_revision(
        sandbox, monkeypatch):
    client, Session, cid, raw = sandbox
    _route(monkeypatch)

    def fail_before_cas(*args, **kwargs):
        raise RuntimeError("synthetic terminal storage fault")

    monkeypatch.setattr(
        "apps.api.main.commit_dependency_revision_in_session", fail_before_cas)
    result = _request(client, cid)
    assert result.status_code == 503, result.text
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(AgentTaskState, cid) is None
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid).count() == 0
        turn = db.query(Turn).filter_by(conversation_id=cid).one()
        response = db.get(Message, turn.assistant_message_id)
        assert turn.state == "partial"
        assert turn.failure_code == "dependency_revision_commit_unconfirmed"
        assert response.status == "partial"
        assert "已原子保存" not in response.content


def test_host_frozen_digest_prevents_mutation_before_admission_record(
        sandbox):
    client, Session, cid, raw = sandbox
    from services.db import Message, Turn
    from agent.primary_host_admission_v1 import (
        PrimaryHostFacts, admit_primary_route_v1,
    )
    from services.primary_route_evidence import record_primary_admission
    proposal = bind(raw=raw)
    decision = admit_primary_route_v1(proposal, PrimaryHostFacts(
        checkpoint=parent(), checkpoint_raw=raw,
        pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True,
        current_request=MESSAGE, dependency_plan_enabled=True,
        dependency_revision_enabled=True))
    assert decision.disposition == "allow"
    assert decision.approved_revision_digest is not None
    tid, mid = str(uuid.uuid4()), str(uuid.uuid4())
    with Session.begin() as db:
        db.add(Message(id=mid, conversation_id=cid, role="user",
                       content=MESSAGE, status="completed"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid, request_id="before-record-1",
                    state="running", user_message_id=mid))
    # Both replacement goals are user-grounded; a changed proposal must
    # still never inherit the first candidate's Host authorization.
    proposal.dependency_revision_steps[1].goal = "第二步的说明"
    with pytest.raises(ValueError,
                       match="primary_revision_candidate_changed_after_host"):
        record_primary_admission(
            session_factory=Session, conversation_id=cid, turn_id=tid,
            preturn_plan_raw=raw, proposal=proposal, decision=decision,
            semantic_attempts=1)
    with Session() as db:
        assert db.get(PrimaryRouteEvidence, tid) is None
        assert db.get(ConversationPlan, cid).snapshot == raw


def test_revision_denied_then_terminal_failure_still_closes_turn(
        sandbox, monkeypatch):
    """Do not select cleanup by the mutable post-CAS termination_reason."""
    client, Session, cid, raw = sandbox
    _route(monkeypatch)

    def deny_revision(*args, **kwargs):
        raise DependencyPlanError("dependency_revision_approval_mismatch")

    def fail_later(*args, **kwargs):
        raise RuntimeError("synthetic failure after revision denial")

    monkeypatch.setattr(
        "apps.api.main.commit_dependency_revision_in_session", deny_revision)
    monkeypatch.setattr(
        "services.plan_store.ConversationPlanStore.release_execution_claim_in_session",
        fail_later)
    response = _request(client, cid)
    assert response.status_code == 503, response.text
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(AgentTaskState, cid) is None
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid).count() == 0
        turn = db.query(Turn).filter_by(conversation_id=cid).one()
        assistant = db.get(Message, turn.assistant_message_id)
        assert turn.state == "partial"
        assert turn.failure_code == "dependency_revision_commit_unconfirmed"
        assert assistant.status == "partial"
        assert "已原子保存" not in assistant.content
