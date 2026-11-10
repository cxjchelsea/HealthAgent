"""S25-06 real PostgreSQL: concurrent mint, generation rollover and old Worker fence."""
import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    Conversation, ConversationPlan, DependencyWorkerFence, PlanExecutionClaim,
    PlanToolDispatch, Turn, database,
)
from services.dependency_fencing import (
    acquire_dependency_fence, assert_dependency_fence_in_session,
    revoke_dependency_fence_in_session,
)
from services.plan_dispatch import PlanDispatchJournal


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires real isolated PostgreSQL")
def test_postgres_worker_single_winner_and_recovered_generation():
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid, tid, next_tid, did = (str(uuid.uuid4()) for _ in range(4))
    raw = '{"plan_schema_version":"dependency-plan-v1"}'
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="fence-owner"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid,
                    request_id="first-" + tid, state="running"))
        db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        db.flush()
        db.add(PlanExecutionClaim(
            conversation_id=cid, turn_id=tid, snapshot=raw))
    try:
        def compete():
            try:
                return acquire_dependency_fence(
                    Session, conversation_id=cid, turn_id=tid,
                    expected_raw=raw)
            except DependencyPlanError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(lambda _: compete(), range(2)))
        token = next(x for x in winners if not isinstance(x, str))
        assert len([x for x in winners if not isinstance(x, str)]) == 1
        assert "dependency_worker_fenced" in winners

        with Session.begin() as db:
            db.add(PlanToolDispatch(
                id=did, conversation_id=cid, turn_id=tid, step_id=1,
                tool_name="mcp_reference_lookup", tool_version="1.0.0",
                arguments_json='{"term":"RAG"}', status="dispatch_unknown",
                worker_generation=token.generation,
                worker_token_digest=hashlib.sha256(
                    token.secret.encode("utf-8")).hexdigest()))
        with Session.begin() as db:
            revoke_dependency_fence_in_session(
                db, conversation_id=cid, turn_id=tid)
            db.get(Turn, tid).state = "partial"
            db.delete(db.get(PlanExecutionClaim, cid))
        journal = PlanDispatchJournal(Session, cid, tid)
        with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
            journal.record_receipt(
                did, {"ok": True, "definition": "RAG"}, fence=token)
        with Session.begin() as db:
            db.add(Turn(id=next_tid, conversation_id=cid,
                        request_id="next-" + next_tid, state="running"))
            db.flush()
            db.add(PlanExecutionClaim(
                conversation_id=cid, turn_id=next_tid, snapshot=raw))
        next_token = acquire_dependency_fence(
            Session, conversation_id=cid, turn_id=next_tid,
            expected_raw=raw)
        assert next_token.generation == token.generation + 1
        with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
            with Session.begin() as db:
                assert_dependency_fence_in_session(
                    db, conversation_id=cid, turn_id=tid, fence=token)
        with Session() as db:
            assert db.get(PlanToolDispatch, did).status == "dispatch_unknown"
            assert db.get(PlanToolDispatch, did).receipt_json is None
            assert db.get(DependencyWorkerFence, cid).turn_id == next_tid
    finally:
        with Session.begin() as db:
            db.execute(delete(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            db.execute(delete(DependencyWorkerFence).where(
                DependencyWorkerFence.conversation_id == cid))
            db.execute(delete(PlanExecutionClaim).where(
                PlanExecutionClaim.conversation_id == cid))
            db.execute(delete(ConversationPlan).where(
                ConversationPlan.conversation_id == cid))
            db.execute(delete(Turn).where(Turn.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
