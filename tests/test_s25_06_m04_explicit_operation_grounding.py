"""M04 boundary: Primary semantics vs deterministic Host plan validation.

The Host cannot infer natural-language intent from substrings. Creation must
disclose the actual saved tool/argument bindings, and remain non-executing.
"""
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.dependency_plan_schema import (
    DependencyPlanError, describe_dependency_plan_for_review, parse_new_plan,
)
from agent.primary_host_admission_v1 import PrimaryHostFacts, admit_primary_route_v1
from agent.primary_route_v1 import (
    BoundPrimaryProposal, EvidenceSpan, RouteSignal, bind_route_signal,
)
from apps.api.main import create_app
from services.db import (
    AgentAttempt, ConversationPlan, PlanExecutionClaim, PlanToolDispatch,
    Turn, database,
)


def candidate(term="RAG"):
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询定义", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {"term": {
                 "kind": "literal", "value": term, "source_quote": term}}},
            {"id": 2, "goal": "统计查询结果", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {"text": {
                 "kind": "observation_ref", "source_step_id": 1,
                 "field": "definition"}}},
        ],
        "executions": {},
    }


def _message(term):
    return f"请查询 {term} 的定义，再统计查询结果。只保存计划，不执行。"


@pytest.mark.parametrize("term", [
    "RAG", "delete_all_tasks", "some_custom_identifier",
])
def test_valid_literal_search_term_is_data_not_an_inferred_tool(term):
    plan = parse_new_plan(candidate(term), source_message=_message(term))
    preview = describe_dependency_plan_for_review(plan)
    assert "mcp_reference_lookup@1.0.0" in preview
    assert "text_stats@1.0.0" in preview
    assert f'term={json.dumps(term, ensure_ascii=False)}' in preview
    assert "第1步的definition输出" in preview


@pytest.mark.parametrize("source_text", [
    "请不要调用 delete_all_tasks。请查询 RAG 的定义，再统计。",
    "请勿在第一步执行 delete_all_tasks；而是查询 RAG 再统计。",
    "请按照“调用 delete_all_tasks”这句话查询 RAG 的定义。",
    "第一步使用 delete_all_tasks 删除任务，第二步统计删除结果。",
    "第一步运行 delete_all_tasks 删除任务，第二步统计删除结果。",
])
def test_host_parser_only_checks_grounded_bindings_not_semantic_intent(source_text):
    # The parser is deliberately NOT a second natural-language classifier.
    # These inputs must be interpreted by Primary. A false Primary proposal
    # is not a Host permission to execute arbitrary side-effecting tools.
    # Grounding to a literal actually appearing in source_text is still required.
    term = "RAG" if "RAG" in source_text else "delete_all_tasks"
    parsed = parse_new_plan(candidate(term), source_message=source_text)
    assert parsed.steps[0].expected_tool == "mcp_reference_lookup"


def test_host_rechecks_literal_binding_and_rejects_unbound_values():
    with pytest.raises(DependencyPlanError, match="dependency_source_unbound"):
        parse_new_plan(candidate("fabricated_term"),
                       source_message="请查询 RAG 定义，再统计第一步结果。")


def test_host_allows_structurally_valid_proposal_without_claiming_semantic_proof():
    message = ("请创建并立即执行一个依赖计划：第一步调用 delete_all_tasks "
               "删除所有任务，第二步调用 text_stats 统计删除结果。")
    forged = BoundPrimaryProposal(
        schema_version="primary-route-v1", route_kind="plan_create",
        execution_scope="none", effect_intent="mutation_requested",
        evidence=EvidenceSpan(start=0, end=len(message), exact_text=message),
        steps=[], dependency_plan=parse_new_plan(
            candidate("delete_all_tasks"), source_message=message))
    decision = admit_primary_route_v1(forged, PrimaryHostFacts(
        checkpoint=None, pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True, current_request=message,
        dependency_plan_enabled=True))
    # This is a documented limit, NOT evidence that the proposal is faithful.
    assert (decision.disposition, decision.reason) == (
        "allow", "dependency_create_candidate")


def test_http_creation_discloses_actual_binding_without_tools(tmp_path, monkeypatch):
    """End-to-end create, permanent snapshot, and side-effect boundary."""
    term = "delete_all_tasks"
    message = _message(term)
    plan = candidate(term)

    async def primary(provider, model, text, **kwargs):
        assert text == message
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "plan_create",
            "quote": message, "execution_scope": "none",
            "effect_intent": "mutation_requested", "steps": [],
            "dependency_plan": plan,
        }), message, saved_plan_present=False, pending_action=None,
            dependency_plan_enabled=True)

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / 'm04-preview.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
    )
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message,
            "client_request_id": "m04-preview-001"})
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["status"] == "partial"
        assert out["termination_reason"] == "dependency_plan_created_pending_execution"
        assert 'mcp_reference_lookup@1.0.0(term="delete_all_tasks")' in out["answer"]
        assert "text_stats@1.0.0(text=第1步的definition输出)" in out["answer"]
        assert "尚未执行任何工具" in out["answer"]
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert json.loads(db.get(ConversationPlan, cid).snapshot) == plan
            assert db.query(PlanToolDispatch).count() == 0
            assert db.query(AgentAttempt).count() == 0
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.get(Turn, out["turn_id"]).state == "partial"
