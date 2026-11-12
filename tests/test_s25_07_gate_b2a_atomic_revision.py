"""S25-07 B2a real SQL transaction tests; no model / tool calls.

PostgreSQL path runs in GitHub Actions with TEST_POSTGRES_URL.
"""
import copy
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete

from agent.dependency_plan_schema import DependencyPlanError
from agent.dependency_revision_candidate import compile_revision_candidate
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyWorkerFence, Message, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)
from services.dependency_revision_commit import commit_dependency_revision_in_session
from services.evidence_store import arguments_digest, bounded_projection
from services.migrate import migrate
from services.primary_route_evidence import snapshot_digest


def _parent():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询 RAG", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0", "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "统计定义", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0", "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ],
        "executions": {},
    }


@pytest.fixture(params=["sqlite", "postgres"])
def seeded(tmp_path, request):
    if request.param == "postgres":
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("requires isolated PostgreSQL TEST_POSTGRES_URL")
    else:
        url = "sqlite:///" + (tmp_path / "b2a.db").as_posix()
        migrate(url)
    engine, Session = database(url)
    cid, tid, mid = (str(uuid.uuid4()) for _ in range(3))
    parent = _parent()
    raw = json.dumps(parent, ensure_ascii=False)
    message_text = "请将第一步改为查询 MCP，第二步保持不变"
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="owner-b2"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid,
                    request_id="req-b2", state="running"))
        db.add(Message(id=mid, conversation_id=cid, role="user",
                       content=message_text))
        db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=tid, conversation_id=cid,
            proposal_id=str(uuid.uuid4()),
            schema_version="primary-route-v1",
            route_kind="plan_revise", semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="dependency_revision_candidate"))
    def proposal_for(raw_parent=raw, text=message_text):
        candidate_steps = [copy.deepcopy(s) for s in json.loads(raw_parent)["steps"]]
        candidate_steps[0]["argument_bindings"]["term"] = {
            "kind": "literal", "value": "MCP", "source_quote": "MCP"}
        candidate_steps[0]["goal"] = "查询 MCP"
        return compile_revision_candidate(
            expected_raw=raw_parent, replacement_pending=candidate_steps,
            current_user_message=text)
    def commit(proposal=None, expected=raw):
        with Session.begin() as db:
            return commit_dependency_revision_in_session(
                db, owner_id="owner-b2", conversation_id=cid,
                turn_id=tid, request_id="req-b2",
                user_message_id=mid, expected_raw=expected,
                proposal=proposal if proposal is not None else proposal_for())
    try:
        yield Session, cid, tid, mid, raw, proposal_for, commit
    finally:
        with Session.begin() as db:
            for model in (
                    AgentStateTransition, AgentAttempt, AgentTaskState,
                    PlanToolDispatch, PrimaryRouteEvidence,
                    PlanExecutionClaim, DependencyWorkerFence,
                    ConversationPlan, Message, Turn):
                db.execute(delete(model).where(model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()


def test_revision_cas_commits_once_and_old_executor_is_not_enabled(seeded):
    Session, cid, *_rest, commit = seeded
    result = commit()
    assert result["revision"] == 2
    assert result["plan_executor_version"] == "dependency-plan-read-replan-v1"
    with Session() as db:
        assert json.loads(db.get(ConversationPlan, cid).snapshot) == result
        task = db.get(AgentTaskState, cid)
        assert task.plan_revision == 2
        assert task.state == "planned"
        assert db.query(PlanToolDispatch).filter_by(conversation_id=cid).count() == 0
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid,
            reason_code="dependency_plan_revision_saved").count() == 1
    with pytest.raises(DependencyPlanError, match="dependency_revision_snapshot_conflict"):
        commit()


def test_second_writer_loses_cas_under_real_postgres_lock(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    if Session.kw["bind"].dialect.name != "postgresql":
        pytest.skip("SQLite has database-wide writer locks; concurrency oracle requires PG")
    proposal = proposal_for()
    def run():
        try:
            return commit(proposal=proposal)["revision"]
        except DependencyPlanError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results.count(2) == 1
    assert results.count("dependency_revision_snapshot_conflict") == 1
    with Session() as db:
        assert db.get(AgentTaskState, cid).plan_revision == 2


def test_claim_and_fence_refuse_revision_without_side_effects(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    with Session.begin() as db:
        db.add(PlanExecutionClaim(conversation_id=cid, turn_id=tid, snapshot=raw))
    with pytest.raises(DependencyPlanError, match="dependency_revision_execution_active"):
        commit()
    with Session.begin() as db:
        db.delete(db.get(PlanExecutionClaim, cid))
        db.add(DependencyWorkerFence(
            conversation_id=cid, turn_id=tid, generation=1,
            token_digest="f" * 64, state="active"))
    with pytest.raises(DependencyPlanError, match="dependency_revision_execution_active"):
        commit()
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(AgentTaskState, cid) is None
        assert db.query(AgentStateTransition).filter_by(conversation_id=cid).count() == 0


def test_pending_dispatch_unknown_or_failure_finalized_is_not_rewritten(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    did = str(uuid.uuid4())
    with Session.begin() as db:
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_json='{"term":"RAG"}',
            status="dispatch_unknown"))
    with pytest.raises(DependencyPlanError, match="dependency_revision_pending_dispatch_exists"):
        commit()
    with Session.begin() as db:
        db.get(PlanToolDispatch, did).status = "failure_finalized"
    with pytest.raises(DependencyPlanError, match="dependency_revision_pending_dispatch_exists"):
        commit()
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw


def test_model_supplied_lineage_and_modified_user_quote_are_rejected(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    proposal = proposal_for()
    proposal.candidate["lineage"]["parent_snapshot_sha256"] = "f" * 64
    with pytest.raises(DependencyPlanError, match="dependency_revision_candidate_tampered"):
        commit(proposal=proposal)
    with Session.begin() as db:
        db.get(Message, mid).content = "只修改步骤显示标题"
    with pytest.raises(DependencyPlanError, match="dependency_source_unbound"):
        commit(proposal=proposal_for())
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw


def test_disposition_and_owner_are_not_assumed_from_proposal(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    with Session.begin() as db:
        db.get(PrimaryRouteEvidence, tid).disposition = "deny"
    with pytest.raises(DependencyPlanError, match="dependency_revision_host_denied"):
        commit()
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw


def test_s25_06_text_stats_new_literal_is_not_committable(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    with Session.begin() as db:
        db.get(Message, mid).content = "请把统计输入改成新文本"
    pending = copy.deepcopy(_parent()["steps"])
    pending[1]["argument_bindings"]["text"] = {
        "kind": "literal", "value": "新文本", "source_quote": "新文本"}
    proposal = compile_revision_candidate(
        expected_raw=raw, replacement_pending=pending,
        current_user_message="请把统计输入改成新文本")
    with pytest.raises(DependencyPlanError, match="dependency_revision_binding_not_executable"):
        commit(proposal=proposal)
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw


def test_verified_completed_prefix_is_preserved_and_tamper_rolls_back(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    data = json.loads(raw)
    data["steps"][0]["status"] = "completed"
    did, origin_tid = str(uuid.uuid4()), str(uuid.uuid4())
    args = {"term": "RAG"}
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    data["executions"]["1"] = {
        "step_id": 1, "plan_revision": 1,
        "dispatch_id": did, "tool_name": "mcp_reference_lookup",
        "tool_version": "1.0.0",
        "resolved_arguments_digest": arguments_digest(args),
        "source_evidence_refs": [],
    }
    completed_raw = json.dumps(data, ensure_ascii=False)
    with Session.begin() as db:
        db.add(Turn(id=origin_tid, conversation_id=cid,
                    request_id="origin-" + origin_tid, state="partial"))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=origin_tid, conversation_id=cid,
            proposal_id=str(uuid.uuid4()),
            schema_version="primary-route-v1",
            route_kind="plan_execute_first", semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
        db.get(ConversationPlan, cid).snapshot = completed_raw
        db.get(PrimaryRouteEvidence, tid).preturn_snapshot_digest = (
            snapshot_digest(completed_raw))
        db.get(Message, mid).content = "请调整第二步的统计说明"
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", status="checkpointed",
            arguments_json=json.dumps(args),
            receipt_json=json.dumps(receipt, ensure_ascii=False)))
        db.add(AgentAttempt(
            id=did, conversation_id=cid, turn_id=origin_tid, run_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_digest=arguments_digest(args),
            status="succeeded",
            result_projection=json.dumps(receipt, ensure_ascii=False)))
        projection = [
            {"id": 1, "status": "completed", "evidence_tool": "mcp_reference_lookup"},
            {"id": 2, "status": "pending", "evidence_tool": None},
        ]
        db.add(AgentTaskState(
            conversation_id=cid, task_id=str(uuid.uuid4()),
            revision=1, plan_revision=1, state="partial",
            step_states=json.dumps(projection, ensure_ascii=False)))
    pending = [copy.deepcopy(data["steps"][1])]
    pending[0]["goal"] = "调整统计说明"
    proposal = compile_revision_candidate(
        expected_raw=completed_raw, replacement_pending=pending,
        current_user_message="请调整第二步的统计说明")
    with Session.begin() as db:
        saved = commit_dependency_revision_in_session(
            db, owner_id="owner-b2", conversation_id=cid,
            turn_id=tid, request_id="req-b2", user_message_id=mid,
            expected_raw=completed_raw, proposal=proposal)
    assert saved["steps"][0] == data["steps"][0]
    assert saved["executions"]["1"] == data["executions"]["1"]
    assert saved["lineage"]["origin_refs"][0]["dispatch_id"] == did
    with Session() as db:
        assert db.get(AgentTaskState, cid).state == "partial"
        assert db.get(AgentTaskState, cid).plan_revision == 2
        assert db.get(AgentAttempt, did).status == "succeeded"
        assert db.get(PlanToolDispatch, did).status == "checkpointed"


def test_completed_prefix_wrong_original_term_is_not_inherited(seeded):
    """A forged but internally digest-consistent history is not plan evidence."""
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    parent = json.loads(raw)
    parent["steps"][0]["status"] = "completed"
    did, origin_tid = str(uuid.uuid4()), str(uuid.uuid4())
    wrong = {"term": "MCP"}
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    digest = arguments_digest(wrong)
    parent["executions"]["1"] = {
        "step_id": 1, "plan_revision": 1,
        "dispatch_id": did, "tool_name": "mcp_reference_lookup",
        "tool_version": "1.0.0",
        "resolved_arguments_digest": digest, "source_evidence_refs": [],
    }
    corrupted_parent = json.dumps(parent, ensure_ascii=False)
    with Session.begin() as db:
        db.add(Turn(id=origin_tid, conversation_id=cid,
                    request_id="origin-" + origin_tid, state="partial"))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=origin_tid, conversation_id=cid,
            proposal_id=str(uuid.uuid4()),
            schema_version="primary-route-v1",
            route_kind="plan_execute_first", semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
        db.get(ConversationPlan, cid).snapshot = corrupted_parent
        db.get(PrimaryRouteEvidence, tid).preturn_snapshot_digest = (
            snapshot_digest(corrupted_parent))
        db.get(Message, mid).content = "调整第二步的统计说明"
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", status="checkpointed",
            arguments_json=json.dumps(wrong),
            receipt_json=json.dumps(receipt, ensure_ascii=False)))
        db.add(AgentAttempt(
            id=did, conversation_id=cid, turn_id=origin_tid, run_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_digest=digest,
            status="succeeded",
            result_projection=json.dumps(receipt, ensure_ascii=False)))
    pending = [copy.deepcopy(parent["steps"][1])]
    pending[0]["goal"] = "调整统计说明"
    proposal = compile_revision_candidate(
        expected_raw=corrupted_parent, replacement_pending=pending,
        current_user_message="调整第二步的统计说明")
    with pytest.raises(DependencyPlanError,
                       match="dependency_revision_lineage_untrusted"):
        with Session.begin() as db:
            commit_dependency_revision_in_session(
                db, owner_id="owner-b2", conversation_id=cid,
                turn_id=tid, request_id="req-b2", user_message_id=mid,
                expected_raw=corrupted_parent, proposal=proposal)
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == corrupted_parent
        assert db.get(AgentTaskState, cid) is None
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid).count() == 0


def test_completed_prefix_fake_source_refs_are_not_inherited(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    parent = json.loads(raw)
    parent["steps"][0]["status"] = "completed"
    did, origin_tid = str(uuid.uuid4()), str(uuid.uuid4())
    args = {"term": "RAG"}
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    parent["executions"]["1"] = {
        "step_id": 1, "plan_revision": 1,
        "dispatch_id": did, "tool_name": "mcp_reference_lookup",
        "tool_version": "1.0.0",
        "resolved_arguments_digest": arguments_digest(args),
        "source_evidence_refs": [{"source_dispatch_id": "fake-parent"}],
    }
    corrupted_parent = json.dumps(parent, ensure_ascii=False)
    with Session.begin() as db:
        db.add(Turn(id=origin_tid, conversation_id=cid,
                    request_id="origin-" + origin_tid, state="partial"))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=origin_tid, conversation_id=cid,
            proposal_id=str(uuid.uuid4()),
            schema_version="primary-route-v1",
            route_kind="plan_execute_first", semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
        db.get(ConversationPlan, cid).snapshot = corrupted_parent
        db.get(PrimaryRouteEvidence, tid).preturn_snapshot_digest = (
            snapshot_digest(corrupted_parent))
        db.get(Message, mid).content = "调整第二步统计说明"
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", status="checkpointed",
            arguments_json=json.dumps(args),
            receipt_json=json.dumps(receipt, ensure_ascii=False)))
        db.add(AgentAttempt(
            id=did, conversation_id=cid, turn_id=origin_tid, run_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_digest=arguments_digest(args),
            status="succeeded",
            result_projection=json.dumps(receipt, ensure_ascii=False)))
    pending = [copy.deepcopy(parent["steps"][1])]
    pending[0]["goal"] = "调整统计说明"
    proposal = compile_revision_candidate(
        expected_raw=corrupted_parent, replacement_pending=pending,
        current_user_message="调整第二步统计说明")
    with pytest.raises(DependencyPlanError,
                       match="dependency_revision_lineage_untrusted"):
        with Session.begin() as db:
            commit_dependency_revision_in_session(
                db, owner_id="owner-b2", conversation_id=cid,
                turn_id=tid, request_id="req-b2", user_message_id=mid,
                expected_raw=corrupted_parent, proposal=proposal)
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == corrupted_parent
        assert db.get(AgentTaskState, cid) is None


def test_missing_task_projection_blocks_completed_prefix_revision(seeded):
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    parent = json.loads(raw)
    parent["steps"][0]["status"] = "completed"
    did, origin_tid = str(uuid.uuid4()), str(uuid.uuid4())
    args = {"term": "RAG"}
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    parent["executions"]["1"] = {
        "step_id": 1, "plan_revision": 1,
        "dispatch_id": did, "tool_name": "mcp_reference_lookup",
        "tool_version": "1.0.0",
        "resolved_arguments_digest": arguments_digest(args),
        "source_evidence_refs": [],
    }
    completed_raw = json.dumps(parent, ensure_ascii=False)
    with Session.begin() as db:
        db.add(Turn(id=origin_tid, conversation_id=cid,
                    request_id="origin-" + origin_tid, state="partial"))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=origin_tid, conversation_id=cid,
            proposal_id=str(uuid.uuid4()),
            schema_version="primary-route-v1",
            route_kind="plan_execute_first", semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
        db.get(ConversationPlan, cid).snapshot = completed_raw
        db.get(PrimaryRouteEvidence, tid).preturn_snapshot_digest = (
            snapshot_digest(completed_raw))
        db.get(Message, mid).content = "调整第二步统计说明"
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", status="checkpointed",
            arguments_json=json.dumps(args),
            receipt_json=json.dumps(receipt, ensure_ascii=False)))
        db.add(AgentAttempt(
            id=did, conversation_id=cid, turn_id=origin_tid, run_id=origin_tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_digest=arguments_digest(args),
            status="succeeded",
            result_projection=json.dumps(receipt, ensure_ascii=False)))
    pending = [copy.deepcopy(parent["steps"][1])]
    pending[0]["goal"] = "调整统计说明"
    proposal = compile_revision_candidate(
        expected_raw=completed_raw, replacement_pending=pending,
        current_user_message="调整第二步统计说明")
    with pytest.raises(DependencyPlanError,
                       match="dependency_revision_task_conflict"):
        with Session.begin() as db:
            commit_dependency_revision_in_session(
                db, owner_id="owner-b2", conversation_id=cid,
                turn_id=tid, request_id="req-b2", user_message_id=mid,
                expected_raw=completed_raw, proposal=proposal)
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == completed_raw
        assert db.get(AgentTaskState, cid) is None


def test_current_revision_turn_cannot_impersonate_past_execution(seeded):
    """An apparently successful dispatch needs an earlier admitted Turn."""
    Session, cid, tid, mid, raw, proposal_for, commit = seeded
    parent = json.loads(raw)
    parent["steps"][0]["status"] = "completed"
    did = str(uuid.uuid4())
    args = {"term": "RAG"}
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    parent["executions"]["1"] = {
        "step_id": 1, "plan_revision": 1, "dispatch_id": did,
        "tool_name": "mcp_reference_lookup", "tool_version": "1.0.0",
        "resolved_arguments_digest": arguments_digest(args),
        "source_evidence_refs": [],
    }
    completed_raw = json.dumps(parent, ensure_ascii=False)
    with Session.begin() as db:
        db.get(ConversationPlan, cid).snapshot = completed_raw
        db.get(PrimaryRouteEvidence, tid).preturn_snapshot_digest = (
            snapshot_digest(completed_raw))
        db.get(Message, mid).content = "调整第二步统计说明"
        # Forged historical outcome is attached to this revision request,
        # which has no execution route. This must never count as a prefix.
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", status="checkpointed",
            arguments_json=json.dumps(args),
            receipt_json=json.dumps(receipt, ensure_ascii=False)))
        db.add(AgentAttempt(
            id=did, conversation_id=cid, turn_id=tid, run_id=tid,
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_digest=arguments_digest(args),
            status="succeeded",
            result_projection=json.dumps(receipt, ensure_ascii=False)))
    pending = [copy.deepcopy(parent["steps"][1])]
    pending[0]["goal"] = "调整统计说明"
    proposal = compile_revision_candidate(
        expected_raw=completed_raw, replacement_pending=pending,
        current_user_message="调整第二步统计说明")
    with pytest.raises(DependencyPlanError,
                       match="dependency_revision_origin_untrusted"):
        with Session.begin() as db:
            commit_dependency_revision_in_session(
                db, owner_id="owner-b2", conversation_id=cid,
                turn_id=tid, request_id="req-b2", user_message_id=mid,
                expected_raw=completed_raw, proposal=proposal)
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == completed_raw
        assert db.get(AgentTaskState, cid) is None
