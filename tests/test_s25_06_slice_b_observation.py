"""S25-06 Slice B: SQLite physical evidence tests, without execution/Host wiring."""
from copy import deepcopy
import json

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, PlanToolDispatch, Turn,
    database,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.dependency_observation import resolve_checkpointed_definition
from services.migrate import migrate


def _plan(digest):
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1,
        "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "completed", "depends_on": [],
             "expected_tool": "mcp_reference_lookup", "tool_version": "1.0.0",
             "argument_bindings": {"term": {"kind": "literal", "value": "RAG",
                                             "source_quote": "RAG"}}},
            {"id": 2, "goal": "statistics", "status": "pending", "depends_on": [1],
             "expected_tool": "text_stats", "tool_version": "1.0.0",
             "argument_bindings": {"text": {"kind": "observation_ref",
                                             "source_step_id": 1,
                                             "field": "definition"}}},
        ],
        "executions": {
            "1": {"dispatch_id": "dispatch-1", "step_id": 1,
                  "plan_revision": 1, "tool_name": "mcp_reference_lookup",
                  "tool_version": "1.0.0",
                  "resolved_arguments_digest": digest,
                  "source_evidence_refs": []}
        },
    }


@pytest.fixture
def case(tmp_path):
    url = "sqlite:///" + (tmp_path / "s2506_b.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    args = {"term": "RAG"}
    digest = arguments_digest(args)
    value = "Retrieval augmented generation"
    receipt = bounded_projection(
        "mcp_reference_lookup", {"ok": True, "definition": value,
                                 "source": "trusted-reference"})
    with Session.begin() as db:
        db.add(Conversation(id="conversation-a", owner_id="owner-a"))
        db.add(Conversation(id="conversation-b", owner_id="owner-b"))
        db.add(ConversationPlan(conversation_id="conversation-a",
                                snapshot=json.dumps(_plan(digest))))
        db.add(Turn(id="turn-1", conversation_id="conversation-a",
                    request_id="test-1", state="completed"))
        db.add(PlanToolDispatch(
            id="dispatch-1", conversation_id="conversation-a",
            turn_id="turn-1", step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            arguments_json=json.dumps(args), status="checkpointed",
            receipt_json=json.dumps(receipt)))
        db.add(AgentAttempt(
            id="dispatch-1", conversation_id="conversation-a",
            turn_id="turn-1", run_id="turn-1", step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            arguments_digest=digest, status="succeeded",
            result_projection=json.dumps(receipt)))
    yield Session
    engine.dispose()


def _resolve(session, **kwargs):
    return resolve_checkpointed_definition(
        session, owner_id=kwargs.get("owner_id", "owner-a"),
        conversation_id=kwargs.get("conversation_id", "conversation-a"),
        target_step_id=kwargs.get("target_step_id", 2))


def _mutate(session, fn):
    with session.begin() as db:
        plan_row = db.get(ConversationPlan, "conversation-a")
        plan = json.loads(plan_row.snapshot)
        dispatch = db.get(PlanToolDispatch, "dispatch-1")
        attempt = db.get(AgentAttempt, "dispatch-1")
        fn(plan, dispatch, attempt)
        plan_row.snapshot = json.dumps(plan)


def _refused(session, code, **kwargs):
    with pytest.raises(DependencyPlanError) as err:
        _resolve(session, **kwargs)
    assert err.value.code == code


def test_genuine_checkpointed_source_resolves_exact_text(case):
    result = _resolve(case)
    assert result.arguments == {"text": "Retrieval augmented generation"}
    assert result.tool_name == "text_stats"
    assert result.arguments_digest == arguments_digest(result.arguments)
    assert result.source_evidence.dispatch_id == "dispatch-1"
    assert result.source_evidence.arguments_digest == arguments_digest({"term": "RAG"})
    assert result.plan_revision == 1


def test_owner_and_conversation_are_verified(case):
    _refused(case, "dependency_untrusted_observation", owner_id="owner-b")
    _refused(case, "dependency_invalid_schema", owner_id="owner-b",
             conversation_id="conversation-b")
    _refused(case, "dependency_untrusted_observation", owner_id="")


@pytest.mark.parametrize("value,code", [
    ("observed", "dependency_untrusted_observation"),
    ("dispatch_unknown", "dependency_untrusted_observation"),
    ("observed_failure", "dependency_untrusted_observation"),
])
def test_no_uncheckpointed_receipt(case, value, code):
    _mutate(case, lambda p, d, a: setattr(d, "status", value))
    _refused(case, code)


def test_absent_attempt_never_claims_success(case):
    _mutate(case, lambda p, d, a: setattr(a, "status", "failed"))
    _refused(case, "dependency_untrusted_observation")


def test_wrong_attempt_digest_fails_closed(case):
    _mutate(case, lambda p, d, a: setattr(a, "arguments_digest", "f" * 64))
    _refused(case, "dependency_untrusted_observation")


def test_wrong_dispatch_tool_version_fails_closed(case):
    _mutate(case, lambda p, d, a: setattr(d, "tool_version", "2.0.0"))
    _refused(case, "dependency_untrusted_observation")


def test_plan_revision_mismatch_blocks_stale_evidence(case):
    _mutate(case, lambda p, d, a: p.update(revision=2))
    _refused(case, "dependency_untrusted_observation")


def test_source_binding_must_match_step_and_digest(case):
    def alter(p, d, a):
        p["executions"]["1"]["resolved_arguments_digest"] = "x" * 64
    _mutate(case, alter)
    _refused(case, "dependency_untrusted_observation")


def test_source_must_be_completed_in_plan(case):
    _mutate(case, lambda p, d, a: p["steps"][0].update(status="pending"))
    _refused(case, "dependency_untrusted_observation")


def test_dependency_must_be_explicit(case):
    _mutate(case, lambda p, d, a: p["steps"][1].update(depends_on=[]))
    _refused(case, "dependency_source_not_dependency")


def test_refusing_cross_conversation_dispatch(case):
    _mutate(case, lambda p, d, a: setattr(d, "conversation_id", "conversation-b"))
    _refused(case, "dependency_untrusted_observation")


def test_truncated_receipt_is_not_a_text_source(case):
    receipt = bounded_projection("mcp_reference_lookup",
                                 {"ok": True, "definition": "a" * 1201})
    def alter(p, d, a):
        d.receipt_json = json.dumps(receipt)
        a.result_projection = json.dumps(receipt)
    _mutate(case, alter)
    _refused(case, "dependency_observation_truncated")


def test_missing_field_is_not_replaced_with_value(case):
    receipt = {"ok": True, "source": "catalog", "value": "not-definition"}
    def alter(p, d, a):
        d.receipt_json = json.dumps(receipt)
        a.result_projection = json.dumps(receipt)
    _mutate(case, alter)
    _refused(case, "dependency_untrusted_observation")


def test_attempt_projection_must_match_receipt(case):
    _mutate(case, lambda p, d, a: setattr(a, "result_projection",
                                          '{"ok":true,"definition":"invented"}'))
    _refused(case, "dependency_untrusted_observation")


def test_not_allowing_target_tool_or_version_swap(case):
    _mutate(case, lambda p, d, a: p["steps"][1].update(expected_tool="create_task"))
    _refused(case, "dependency_version_mismatch")


def test_runtime_does_not_execute_or_mutate_plan(case):
    before = _resolve(case)
    after = _resolve(case)
    assert before == after
    with case() as db:
        dispatch = db.get(PlanToolDispatch, "dispatch-1")
        assert dispatch.status == "checkpointed"
        assert db.get(ConversationPlan, "conversation-a").snapshot is not None
