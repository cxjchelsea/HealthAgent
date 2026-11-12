"""B3a physical lineage verification, no execution or Claim."""
import copy
import json
import os
import uuid

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from agent.dependency_revision_candidate import compile_revision_candidate
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, Message, PlanExecutionClaim, PlanToolDispatch,
    PrimaryRouteEvidence, Turn,
    database,
)
from services.dependency_revision_fingerprint import revision_fingerprint
from services.dependency_revision_lineage import verify_revised_lineage
from services.evidence_store import arguments_digest, bounded_projection
from services.migrate import migrate


def _id():
    return str(uuid.uuid4())


def _parent(completed=False):
    result = {
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
                 "text": {"kind": "observation_ref", "source_step_id": 1,
                          "field": "definition"}}},
        ], "executions": {},
    }
    if completed == 2:
        result["steps"].append({
            "id": 3, "goal": "查询 MCP", "status": "pending",
            "depends_on": [], "expected_tool": "mcp_reference_lookup",
            "tool_version": "1.0.0", "argument_bindings": {
                "term": {"kind": "literal", "value": "MCP",
                         "source_quote": "MCP"}}})
        result["next_step_id"] = 4
        result["steps"][1]["status"] = "completed"
    if completed:
        result["steps"][0]["status"] = "completed"
    return result


@pytest.fixture(params=["sqlite", "postgres"])
def store(tmp_path, request):
    if request.param == "postgres":
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("requires isolated TEST_POSTGRES_URL")
    else:
        url = "sqlite:///" + (tmp_path / "b3a.db").as_posix()
        migrate(url)
    engine, Session = database(url)
    cid = _id()
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="b3a-owner"))
    try:
        yield Session, cid
    finally:
        from sqlalchemy import delete
        with Session.begin() as db:
            for model in (AgentStateTransition, AgentTaskState, AgentAttempt,
                          PlanExecutionClaim, PlanToolDispatch,
                          PrimaryRouteEvidence, ConversationPlan, Turn, Message):
                db.execute(delete(model).where(model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()


def seed(Session, cid, *, completed=False):
    parent = _parent(completed)
    origin_turn = None
    dispatch_id = None
    if completed:
        origin_turn, dispatch_id = _id(), _id()
        args = {"term": "RAG"}
        parent["executions"]["1"] = {
            "step_id": 1, "plan_revision": 1, "dispatch_id": dispatch_id,
            "tool_name": "mcp_reference_lookup", "tool_version": "1.0.0",
            "resolved_arguments_digest": arguments_digest(args),
            "source_evidence_refs": [],
        }
    if completed == 2:
        origin_stats_turn, stats_dispatch_id = _id(), _id()
        definition = "retrieval augmented generation"
        stats_args = {"text": definition}
        parent["executions"]["2"] = {
            "step_id": 2, "plan_revision": 1,
            "dispatch_id": stats_dispatch_id, "tool_name": "text_stats",
            "tool_version": "1.0.0",
            "resolved_arguments_digest": arguments_digest(stats_args),
            "source_evidence_refs": [{"source_dispatch_id": dispatch_id}],
        }
    raw = json.dumps(parent, ensure_ascii=False)
    message = ("请将第三步目标改为查询 MCP 文档，不要执行"
               if completed == 2 else
               "请将第二步说明改为统计定义字符数，不要执行")
    pending = copy.deepcopy([
        s for s in parent["steps"] if s["status"] == "pending"])
    pending[-1]["goal"] = ("查询 MCP 文档" if completed == 2
                            else "统计定义字符数")
    candidate = compile_revision_candidate(
        expected_raw=raw, replacement_pending=pending,
        current_user_message=message).candidate
    approved_turn, mid, assistant_mid, task_id = _id(), _id(), _id(), _id()
    with Session.begin() as db:
        if completed:
            receipt = bounded_projection("mcp_reference_lookup", {
                "ok": True, "definition": "retrieval augmented generation",
                "source": "trusted-reference"})
            db.add(Turn(id=origin_turn, conversation_id=cid,
                        request_id="origin-"+origin_turn, state="partial"))
            db.flush()
            db.add(PrimaryRouteEvidence(
                turn_id=origin_turn, conversation_id=cid,
                proposal_id=_id(), schema_version="primary-route-v1",
                route_kind="plan_execute_first", semantic_attempts=1,
                preturn_snapshot_digest="b"*64, disposition="allow",
                admission_reason="primary_v1_plan_execution_candidate"))
            db.add(PlanToolDispatch(
                id=dispatch_id, conversation_id=cid, turn_id=origin_turn,
                step_id=1, tool_name="mcp_reference_lookup",
                tool_version="1.0.0", arguments_json=json.dumps({"term":"RAG"}),
                status="checkpointed",
                receipt_json=json.dumps(receipt)))
            db.add(AgentAttempt(
                id=dispatch_id, conversation_id=cid, turn_id=origin_turn,
                run_id=origin_turn, step_id=1, tool_name="mcp_reference_lookup",
                tool_version="1.0.0", arguments_digest=arguments_digest(
                    {"term":"RAG"}), status="succeeded",
                result_projection=json.dumps(receipt)))
            if completed == 2:
                stats_receipt = bounded_projection("text_stats", {
                    "ok": True, "characters": len(definition),
                    "words": len(definition.split())})
                db.add(Turn(id=origin_stats_turn, conversation_id=cid,
                            request_id="origin-"+origin_stats_turn,
                            state="partial"))
                db.flush()
                db.add(PrimaryRouteEvidence(
                    turn_id=origin_stats_turn, conversation_id=cid,
                    proposal_id=_id(), schema_version="primary-route-v1",
                    route_kind="plan_resume", semantic_attempts=1,
                    preturn_snapshot_digest="c"*64, disposition="allow",
                    admission_reason="primary_v1_plan_execution_candidate"))
                db.add(PlanToolDispatch(
                    id=stats_dispatch_id, conversation_id=cid,
                    turn_id=origin_stats_turn, step_id=2,
                    tool_name="text_stats", tool_version="1.0.0",
                    arguments_json=json.dumps(stats_args),
                    status="checkpointed",
                    receipt_json=json.dumps(stats_receipt)))
                db.add(AgentAttempt(
                    id=stats_dispatch_id, conversation_id=cid,
                    turn_id=origin_stats_turn, run_id=origin_stats_turn,
                    step_id=2, tool_name="text_stats", tool_version="1.0.0",
                    arguments_digest=arguments_digest(stats_args),
                    status="succeeded",
                    result_projection=json.dumps(stats_receipt)))
        db.add(Message(id=mid, conversation_id=cid, role="user",
                       content=message, status="completed"))
        db.add(Message(id=assistant_mid, conversation_id=cid, role="assistant",
                       content="修订已保存", status="partial"))
        db.flush()
        db.add(Turn(id=approved_turn, conversation_id=cid,
                    request_id="revision-"+approved_turn,
                    user_message_id=mid, assistant_message_id=assistant_mid,
                    state="partial"))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=approved_turn, conversation_id=cid, proposal_id=_id(),
            schema_version="primary-route-v1", route_kind="plan_revise",
            semantic_attempts=1, preturn_snapshot_digest="a"*64,
            disposition="allow",
            admission_reason="dependency_revision_candidate",
            approved_revision_digest=revision_fingerprint(candidate)))
        db.add(ConversationPlan(
            conversation_id=cid, snapshot=json.dumps(candidate, ensure_ascii=False)))
        db.add(AgentTaskState(
            conversation_id=cid, task_id=task_id, revision=2,
            plan_revision=2, state="partial" if completed else "planned",
            step_states=json.dumps([{
                "id": x["id"], "status": x["status"],
                "evidence_tool": x["expected_tool"]
                if x["status"] == "completed" else None
            } for x in candidate["steps"]], ensure_ascii=False)))
        db.add(AgentStateTransition(
            id=_id(), conversation_id=cid, task_id=task_id, revision=2,
            entity_type="task", from_state="planned", to_state="partial"
            if completed else "planned", reason_code="dependency_plan_revision_saved"))
    return json.dumps(candidate, ensure_ascii=False), approved_turn, dispatch_id


@pytest.mark.parametrize("completed", [False, True])
def test_authenticated_revision_lineage_proves_physical_prefix(store, completed):
    Session, cid = store
    raw, tid, did = seed(Session, cid, completed=completed)
    proof = verify_revised_lineage(
        Session, owner_id="b3a-owner", conversation_id=cid,
        expected_raw=raw)
    assert proof.revision_turn_id == tid
    assert proof.preserved_step_ids == ((1,) if completed else ())
    assert proof.next_step_id == (2 if completed else 1)
    with pytest.raises(DependencyPlanError):
        verify_revised_lineage(Session, owner_id="other", conversation_id=cid,
                                expected_raw=raw)


@pytest.mark.parametrize("tamper", [
    "approved_digest", "approval_disposition", "task_revision",
    "task_projection", "lineage_origin", "origin_dispatch",
    "extra_dispatch", "snapshot", "attempt_projection", "origin_host",
    "revision_user_role", "revision_message_id", "revision_schema",
    "revision_assistant", "origin_schema", "revision_transition_state",
    "revision_transition_attempt",
])
def test_forged_or_stale_revision_lineage_rejected(store, tamper):
    Session, cid = store
    raw, tid, did = seed(Session, cid, completed=True)
    expected = raw
    with Session.begin() as db:
        evidence = db.get(PrimaryRouteEvidence, tid)
        task = db.get(AgentTaskState, cid)
        if tamper == "approved_digest":
            evidence.approved_revision_digest = "f"*64
        elif tamper == "approval_disposition":
            evidence.disposition = "deny"
        elif tamper == "task_revision":
            task.plan_revision = 1
        elif tamper == "task_projection":
            task.step_states = "[]"
        elif tamper == "origin_dispatch":
            db.get(PlanToolDispatch, did).arguments_json = '{"term":"other"}'
        elif tamper == "attempt_projection":
            db.get(AgentAttempt, did).result_projection = '{}'
        elif tamper == "origin_host":
            db.get(PrimaryRouteEvidence,
                   db.get(PlanToolDispatch, did).turn_id).disposition = "deny"
        elif tamper == "origin_schema":
            db.get(PrimaryRouteEvidence,
                   db.get(PlanToolDispatch, did).turn_id).schema_version = "legacy"
        elif tamper == "revision_schema":
            evidence.schema_version = "legacy"
        elif tamper == "revision_user_role":
            db.get(Message, db.get(Turn, tid).user_message_id).role = "assistant"
        elif tamper == "revision_message_id":
            db.get(Turn, tid).user_message_id = None
        elif tamper == "revision_assistant":
            db.get(Message, db.get(Turn, tid).assistant_message_id).status = "pending"
        elif tamper == "revision_transition_state":
            db.query(AgentStateTransition).filter_by(
                conversation_id=cid,
                reason_code="dependency_plan_revision_saved").one().to_state = "completed"
        elif tamper == "revision_transition_attempt":
            db.query(AgentStateTransition).filter_by(
                conversation_id=cid,
                reason_code="dependency_plan_revision_saved").one().attempt_id = _id()
        elif tamper == "extra_dispatch":
            db.add(PlanToolDispatch(
                id=_id(), conversation_id=cid, turn_id=tid, step_id=2,
                tool_name="text_stats", tool_version="1.0.0",
                arguments_json="{}", status="dispatch_unknown"))
        else:
            altered = json.loads(raw)
            if tamper == "lineage_origin":
                altered["lineage"]["origin_refs"][0]["dispatch_id"] = _id()
            else:
                altered["steps"][1]["goal"] = "forged new goal"
            expected = json.dumps(altered, ensure_ascii=False)
            db.get(ConversationPlan, cid).snapshot = expected
            if tamper == "lineage_origin":
                # Even if a compromised DB row supplies a matching candidate
                # hash, the physical Dispatch/Attempt origin remains binding.
                evidence.approved_revision_digest = revision_fingerprint(altered)
    with pytest.raises(DependencyPlanError,
                       match="dependency_revision_lineage_untrusted"):
        verify_revised_lineage(
            Session, owner_id="b3a-owner", conversation_id=cid,
            expected_raw=expected)


def test_original_read_executor_still_does_not_accept_revision2(store):
    from agent.dependency_plan_host import _step_for_read
    Session, cid = store
    raw, _, _ = seed(Session, cid)
    with pytest.raises(DependencyPlanError):
        _step_for_read(json.loads(raw), 1)


def test_inherited_two_completed_steps_keep_original_revision1_evidence(store):
    Session, cid = store
    raw, tid, did = seed(Session, cid, completed=2)
    proof = verify_revised_lineage(
        Session, owner_id="b3a-owner", conversation_id=cid,
        expected_raw=raw)
    assert proof.preserved_step_ids == (1, 2)
    assert proof.next_step_id == 3
    with Session.begin() as db:
        stats = db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, step_id=2).one()
        original = json.loads(stats.receipt_json)
        original["characters"] += 1
        stats.receipt_json = json.dumps(original)
        # Keep Attempt projection consistent with the forged receipt: the
        # verifier must independently reject the impossible character count.
        db.get(AgentAttempt, stats.id).result_projection = json.dumps(
            bounded_projection("text_stats", original))
    with pytest.raises(DependencyPlanError,
                       match="dependency_revision_lineage_untrusted"):
        verify_revised_lineage(
            Session, owner_id="b3a-owner", conversation_id=cid,
            expected_raw=raw)


# S25-07 B3b -- internal Host/Observation preparation. This deliberately uses
# the physically seeded B3a revision-2 history, not a mock lineage object.

def _seed_revision_resume(Session, cid, raw):
    from services.db import PlanExecutionClaim
    from services.primary_route_evidence import snapshot_digest
    message_text = "请继续执行修订后的已保存计划"
    mid, tid = _id(), _id()
    with Session.begin() as db:
        db.add(Message(id=mid, conversation_id=cid, role="user",
                       content=message_text, status="completed"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid,
                    request_id="b3b-"+tid, user_message_id=mid,
                    state="running"))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=tid, conversation_id=cid, proposal_id=_id(),
            schema_version="primary-route-v1", route_kind="plan_resume",
            semantic_attempts=1, preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="dependency_revision_resume_prepare_only"))
        db.add(PlanExecutionClaim(
            conversation_id=cid, turn_id=tid, snapshot=raw))
    return tid, message_text


def _b3b_settings(enabled=True):
    from agent.config import Settings
    return Settings(
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
        s25_07_revision_resume_prepare_enabled=enabled)


def _b3b_host(raw, message_text, enabled=True, *,
              kind="plan_resume", scope="remaining_steps"):
    from agent.primary_route_v1 import RouteSignal, bind_route_signal
    from agent.primary_host_admission_v1 import (
        PrimaryHostFacts, admit_primary_route_v1)
    proposal = bind_route_signal(
        RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": kind,
            "quote": message_text, "effect_intent": "read_only",
            "execution_scope": scope,
        }), message_text, saved_plan_present=True)
    decision = admit_primary_route_v1(proposal, PrimaryHostFacts(
        checkpoint=json.loads(raw), checkpoint_raw=raw,
        pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True, current_request=message_text,
        dependency_plan_enabled=True,
        revision_resume_prepare_enabled=enabled))
    return decision


@pytest.mark.parametrize("completed", [False, True])
def test_b3b_internal_host_binds_first_pending_without_dispatch(store, completed):
    from agent.dependency_revision_host import bind_revised_plan_read
    from services.db import PlanExecutionClaim
    Session, cid = store
    raw, revision_tid, historical_dispatch_id = seed(
        Session, cid, completed=completed)
    tid, message_text = _seed_revision_resume(Session, cid, raw)
    decision = _b3b_host(raw, message_text)
    assert decision.disposition == "allow"
    assert decision.reason == "dependency_revision_resume_prepare_only"
    prepared = bind_revised_plan_read(
        Session, settings=_b3b_settings(), decision=decision,
        owner_id="b3a-owner", conversation_id=cid, turn_id=tid,
        request_id="b3b-"+tid, expected_raw=raw, initial_turn_raw=raw,
        step_id=2 if completed else 1)
    assert prepared.plan_revision == 2
    assert prepared.approved_revision_turn_id == revision_tid
    assert prepared.arguments == (
        {"text": "retrieval augmented generation"}
        if completed else {"term": "RAG"})
    assert prepared.inherited_source_dispatch_id == (
        historical_dispatch_id if completed else None)
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(PlanExecutionClaim, cid).snapshot == raw
        assert db.query(PlanToolDispatch).filter_by(
            conversation_id=cid).count() == (1 if completed else 0)
        assert db.query(AgentAttempt).filter_by(
            conversation_id=cid).count() == (1 if completed else 0)


def test_b3b_default_off_and_execute_first_never_get_revision2_authority(store):
    from agent.dependency_revision_host import bind_revised_plan_read
    Session, cid = store
    raw, _, _ = seed(Session, cid, completed=True)
    tid, text = _seed_revision_resume(Session, cid, raw)
    assert _b3b_host(raw, text, enabled=False).disposition == "deny"
    assert _b3b_host(raw, text, kind="plan_execute_first",
                     scope="first_step").disposition == "deny"
    with pytest.raises(DependencyPlanError):
        bind_revised_plan_read(
            Session, settings=_b3b_settings(False),
            decision=_b3b_host(raw, text), owner_id="b3a-owner",
            conversation_id=cid, turn_id=tid, request_id="b3b-"+tid,
            expected_raw=raw, initial_turn_raw=raw, step_id=2)


@pytest.mark.parametrize("tamper", [
    "wrong_step", "wrong_owner", "wrong_request", "claim_mismatch",
    "claim_missing", "receipt_changed", "attempt_changed", "host_denied",
    "plan_changed", "inherited_source_changed",
])
def test_b3b_rejects_forged_lineage_or_claim(store, tamper):
    from agent.dependency_revision_host import bind_revised_plan_read
    from services.db import PlanExecutionClaim
    Session, cid = store
    raw, _, source_id = seed(Session, cid, completed=True)
    tid, message_text = _seed_revision_resume(Session, cid, raw)
    decision = _b3b_host(raw, message_text)
    step, owner, request = 2, "b3a-owner", "b3b-"+tid
    with Session.begin() as db:
        if tamper == "wrong_step":
            step = 1
        elif tamper == "wrong_owner":
            owner = "attacker"
        elif tamper == "wrong_request":
            request = "replayed-other-request"
        elif tamper == "claim_mismatch":
            db.get(PlanExecutionClaim, cid).snapshot = "{}"
        elif tamper == "claim_missing":
            db.delete(db.get(PlanExecutionClaim, cid))
        elif tamper == "receipt_changed":
            db.get(PlanToolDispatch, source_id).receipt_json = '{"ok":true,"definition":"forged"}'
        elif tamper == "attempt_changed":
            db.get(AgentAttempt, source_id).result_projection = "{}"
        elif tamper == "host_denied":
            db.get(PrimaryRouteEvidence, tid).disposition = "deny"
        elif tamper == "plan_changed":
            db.get(ConversationPlan, cid).snapshot = "{}"
        elif tamper == "inherited_source_changed":
            db.get(PlanToolDispatch, source_id).arguments_json = '{"term":"MCP"}'
    with pytest.raises(DependencyPlanError):
        bind_revised_plan_read(
            Session, settings=_b3b_settings(), decision=decision,
            owner_id=owner, conversation_id=cid, turn_id=tid,
            request_id=request, expected_raw=raw, initial_turn_raw=raw,
            step_id=step)


def test_b3b_inert_prepared_result_is_rejected_by_v1_dispatch(store):
    from agent.dependency_revision_host import bind_revised_plan_read
    from services.dependency_dispatch import begin_dependency_dispatch_in_session
    Session, cid = store
    raw, _, _ = seed(Session, cid, completed=False)
    tid, text = _seed_revision_resume(Session, cid, raw)
    prepared = bind_revised_plan_read(
        Session, settings=_b3b_settings(), decision=_b3b_host(raw, text),
        owner_id="b3a-owner", conversation_id=cid, turn_id=tid,
        request_id="b3b-"+tid, expected_raw=raw, initial_turn_raw=raw,
        step_id=1)
    with Session.begin() as db:
        with pytest.raises(DependencyPlanError,
                           match="dependency_execution_binding_invalid"):
            begin_dependency_dispatch_in_session(
                db, binding=prepared, expected_raw=raw,
                initial_turn_raw=raw)
        assert db.query(PlanToolDispatch).filter_by(
            conversation_id=cid).count() == 0
