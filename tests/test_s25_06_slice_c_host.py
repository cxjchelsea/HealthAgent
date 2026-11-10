"""S25-06 Slice C isolated Host preparation: no M4 dispatch or tool invocation."""
import json

import pytest

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError
from agent.dependency_plan_host import (
    bind_dependency_plan_read, plan_read_adapter_schema,
)
from agent.primary_host_admission_v1 import PrimaryHostDecision
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, PlanExecutionClaim,
    PlanToolDispatch, Turn, database,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.migrate import migrate


FIRST = PrimaryHostDecision(
    "allow", "primary_v1_plan_execution_candidate",
    "plan_execute_first", "read_only", "execute_first")
RESUME = PrimaryHostDecision(
    "allow", "primary_v1_plan_execution_candidate",
    "plan_resume", "read_only", "resume")


def _plan(completed=False):
    args = {"term": "RAG"}
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1,
        "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "completed" if completed else "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0", "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}}},
            {"id": 2, "goal": "stats", "status": "pending", "depends_on": [1],
             "expected_tool": "text_stats", "tool_version": "1.0.0",
             "argument_bindings": {"text": {
                 "kind": "observation_ref", "source_step_id": 1,
                 "field": "definition"}}},
        ],
        "executions": ({
            "1": {"dispatch_id": "dispatch1", "step_id": 1, "plan_revision": 1,
                  "tool_name": "mcp_reference_lookup", "tool_version": "1.0.0",
                  "resolved_arguments_digest": arguments_digest(args)}
        } if completed else {}),
    }


@pytest.fixture
def case(tmp_path):
    url = "sqlite:///" + (tmp_path / "slice_c.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    initial = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="owner"))
        db.add(Conversation(id="other", owner_id="other_owner"))
        db.add(ConversationPlan(conversation_id="cid", snapshot=initial))
        db.add(Turn(id="tid", conversation_id="cid", request_id="rid", state="running"))
        db.add(PlanExecutionClaim(conversation_id="cid", turn_id="tid", snapshot=initial))
    yield Session
    engine.dispose()


def _settings(**overrides):
    return Settings(
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True, **overrides)


def _call(Session, *, settings=None, decision=FIRST, control="execute_first",
          step_id=1, owner_id="owner", conversation_id="cid",
          turn_id="tid", request_id="rid", expected_raw=None):
    if expected_raw is None:
        with Session() as db:
            expected_raw = db.get(ConversationPlan, "cid").snapshot
    return bind_dependency_plan_read(
        Session, settings=settings or _settings(), decision=decision,
        owner_id=owner_id, conversation_id=conversation_id,
        turn_id=turn_id, request_id=request_id, expected_raw=expected_raw,
        control=control, step_id=step_id)


def _denied(Session, code, **kwargs):
    with pytest.raises(DependencyPlanError) as err:
        _call(Session, **kwargs)
    assert err.value.code == code


def _change(Session, fn):
    with Session.begin() as db:
        fn(db)


def _complete_source(Session):
    args = {"term": "RAG"}
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    with Session.begin() as db:
        row = db.get(ConversationPlan, "cid")
        snapshot = _plan(completed=True)
        raw = json.dumps(snapshot, ensure_ascii=False)
        row.snapshot = raw
        db.get(PlanExecutionClaim, "cid").snapshot = raw
        db.add(PlanToolDispatch(
            id="dispatch1", conversation_id="cid", turn_id="tid",
            step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_json=json.dumps(args),
            status="checkpointed", receipt_json=json.dumps(receipt)))
        db.add(AgentAttempt(
            id="dispatch1", conversation_id="cid", turn_id="tid",
            run_id="tid", step_id=1, tool_name="mcp_reference_lookup",
            tool_version="1.0.0", arguments_digest=arguments_digest(args),
            status="succeeded", result_projection=json.dumps(receipt)))


def test_first_step_bound_to_host_claim_and_exact_version(case):
    binding = _call(case)
    assert binding.arguments == {"term": "RAG"}
    assert binding.tool_name == "mcp_reference_lookup"
    assert binding.tool_version == "1.0.0"
    assert len(binding.checkpoint_digest) == 64
    assert plan_read_adapter_schema(binding)["function"]["name"] == "mcp_reference_lookup"
    with case() as db:
        assert db.query(PlanToolDispatch).count() == 0


def test_resume_prepares_persisted_trusted_observation_without_dispatch(case):
    _complete_source(case)
    binding = _call(case, decision=RESUME, control="resume", step_id=2)
    assert binding.arguments == {"text": "retrieval augmented generation"}
    assert binding.source_dispatch_id == "dispatch1"
    assert plan_read_adapter_schema(binding)["function"]["name"] == "text_stats"
    with case() as db:
        assert db.query(PlanToolDispatch).count() == 1
        assert db.get(PlanToolDispatch, "dispatch1").status == "checkpointed"


def test_gate_defaults_off_and_no_grants_from_ordinary_registry(case):
    _denied(case, "dependency_host_disabled",
            settings=Settings(model_provider="fake", routing_profile="unified",
                              primary_route_v1_canary_enabled=True))
    _denied(case, "dependency_host_disabled",
            settings=Settings(model_provider="fake", routing_profile="legacy",
                              primary_route_v1_canary_enabled=True,
                              s25_06_dependency_plan_enabled=True))


@pytest.mark.parametrize("changed", [
    {"owner_id": "other_owner"},
    {"conversation_id": "other"},
    {"turn_id": "wrong"},
    {"request_id": "wrong"},
])
def test_scope_binding_fails_closed(case, changed):
    _denied(case, "dependency_host_claim_denied", **changed)


def test_pending_step_cannot_skip_first(case):
    _denied(case, "dependency_step_not_ready", step_id=2)


@pytest.mark.parametrize("decision,control", [
    (RESUME, "execute_first"),
    (FIRST, "resume"),
    (PrimaryHostDecision("deny", "primary_v1_plan_execution_candidate",
                         "plan_execute_first", "read_only", "execute_first"),
     "execute_first"),
    (PrimaryHostDecision("allow", "primary_v1_answer_only",
                         "respond", "read_only"), "execute_first"),
])
def test_model_or_denied_host_cannot_grant_plan(case, decision, control):
    _denied(case, "dependency_host_control_denied",
            decision=decision, control=control)


def test_claim_must_match_snapshot_and_running_turn(case):
    def bad_claim(db):
        db.get(PlanExecutionClaim, "cid").snapshot = "stale"
    _change(case, bad_claim)
    _denied(case, "dependency_host_claim_denied")


def test_terminal_turn_cannot_prepare(case):
    _change(case, lambda db: setattr(db.get(Turn, "tid"), "state", "completed"))
    _denied(case, "dependency_host_claim_denied")


def test_unknown_executor_version_cannot_prepare(case):
    def mutate(db):
        row = db.get(ConversationPlan, "cid")
        p = json.loads(row.snapshot)
        p["plan_executor_version"] = "unknown"
        raw = json.dumps(p)
        row.snapshot = raw
        db.get(PlanExecutionClaim, "cid").snapshot = raw
    _change(case, mutate)
    _denied(case, "dependency_version_mismatch")


def test_source_without_checkpointed_receipt_fails_closed(case):
    _complete_source(case)
    _change(case, lambda db: setattr(db.get(PlanToolDispatch, "dispatch1"),
                                     "status", "observed"))
    _denied(case, "dependency_untrusted_observation",
            decision=RESUME, control="resume", step_id=2)


def test_tool_name_cannot_be_ordinary_or_write(case):
    def change_tool(db):
        row = db.get(ConversationPlan, "cid")
        p = json.loads(row.snapshot)
        p["steps"][0]["expected_tool"] = "create_task"
        raw = json.dumps(p)
        row.snapshot = raw
        db.get(PlanExecutionClaim, "cid").snapshot = raw
    _change(case, change_tool)
    _denied(case, "dependency_invalid_schema")


def test_no_permissions_from_preview_object(case):
    from agent.tool_registry import READ_REGISTRY
    binding = _call(case)
    result = READ_REGISTRY.invoke(
        binding.tool_name, binding.arguments, expected_version=binding.tool_version,
        granted_names=(), exposed_names=(binding.tool_name,))
    assert result["error"] == "permission_denied"


def _replace_plan(case, mutate):
    def change(db):
        row = db.get(ConversationPlan, "cid")
        plan = json.loads(row.snapshot)
        mutate(plan)
        raw = json.dumps(plan, ensure_ascii=False)
        row.snapshot = raw
        db.get(PlanExecutionClaim, "cid").snapshot = raw
    _change(case, change)


@pytest.mark.parametrize("mutation,code", [
    (lambda p: p["steps"][1].update(id=1), "dependency_invalid_schema"),
    (lambda p: p["steps"][1].update(depends_on=[1, 1]), "dependency_invalid_schema"),
    (lambda p: p["steps"][0].update(depends_on=[2]), "dependency_invalid_schema"),
    (lambda p: p["steps"][1].update(expected_tool="create_task"), "dependency_invalid_schema"),
    (lambda p: p["steps"][1]["argument_bindings"]["text"].update(source_step_id=77),
     "dependency_invalid_schema"),
    (lambda p: p["steps"][1].update(unexpected="host-skip"), "dependency_invalid_schema"),
    (lambda p: p.update(next_step_id=99), "dependency_invalid_schema"),
    (lambda p: p.update(executions={"1": {"dispatch_id": "forged"}}),
     "dependency_untrusted_observation"),
    (lambda p: p["steps"][1].update(status="completed"), "dependency_invalid_schema"),
    (lambda p: p["steps"][0]["argument_bindings"]["term"].update(value="injected"),
     "dependency_invalid_schema"),
])
def test_full_plan_rejects_tampering_outside_selected_step(case, mutation, code):
    _replace_plan(case, mutation)
    _denied(case, code)


def test_completed_step_requires_bound_execution_record(case):
    _complete_source(case)
    _replace_plan(case, lambda p: p.update(executions={}))
    _denied(case, "dependency_untrusted_observation",
            decision=RESUME, control="resume", step_id=2)


def test_completed_step_cannot_follow_pending_step(case):
    _complete_source(case)
    def invert(p):
        p["steps"][0]["status"] = "pending"
        p["steps"][1]["status"] = "completed"
    _replace_plan(case, invert)
    _denied(case, "dependency_invalid_schema",
            decision=RESUME, control="resume", step_id=1)


def test_unknown_plan_fields_and_null_ids_are_denied(case):
    _replace_plan(case, lambda p: p.update(extra_field="unsafe"))
    _denied(case, "dependency_invalid_schema")
