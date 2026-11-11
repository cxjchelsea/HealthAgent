"""M04 guard: literal unsupported operation identifiers cannot be laundered
into a permitted dependent reference plan.

This is a syntactic contract for explicit machine-readable identifiers, NOT a
general natural-language semantic entailment classifier.
"""
import pytest

from agent.dependency_plan_schema import DependencyPlanError, parse_new_plan
from agent.primary_host_admission_v1 import PrimaryHostFacts, admit_primary_route_v1


def candidate(term: str) -> dict:
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1,
        "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询指定标识符定义", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {"term": {
                 "kind": "literal", "value": term,
                 "source_quote": term}}},
            {"id": 2, "goal": "统计定义文本", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {"text": {
                 "kind": "observation_ref", "source_step_id": 1,
                 "field": "definition"}}},
        ], "executions": {},
    }


def test_m04_unsupported_named_operation_cannot_become_reference_term():
    message = ("请创建并立即执行一个依赖计划：第一步调用 delete_all_tasks "
               "删除所有任务，第二步调用 text_stats 统计删除结果。")
    with pytest.raises(
            DependencyPlanError, match="dependency_unsupported_operation_identifier"):
        parse_new_plan(candidate("delete_all_tasks"), source_message=message)


def test_valid_named_read_only_dependency_still_admitted():
    message = ("请使用 mcp_reference_lookup 查询 RAG 的定义，再将查询结果"
               "通过 text_stats 统计字符数；只保存计划，不执行。")
    plan = candidate("RAG")
    parsed = parse_new_plan(plan, source_message=message)
    assert parsed.steps[1].argument_bindings["text"].source_step_id == 1


def test_bare_snake_case_lookup_term_is_legitimate_data():
    # A term with underscores alone must NEVER be treated as an invocation.
    for term in ("some_custom_identifier", "delete_all_tasks", "call_model"):
        message = f"请查询 {term} 的定义，再统计返回文本。只保存计划，不执行。"
        assert parse_new_plan(candidate(term), source_message=message)


@pytest.mark.parametrize("request", [
    "第一步调用 delete_all_tasks 删除所有任务，第二步调用 text_stats 统计。",
    "Please call delete_all_tasks to delete all tasks, then text_stats.",
    "Please invoke delete_all_tasks to delete all tasks.",
    "第一步执行工具 tool:delete_all_tasks，再统计结果。",
    "请先执行 delete_all_tasks()，再统计删除结果。",
])
def test_explicit_unsupported_invocations_are_rejected(request):
    with pytest.raises(
            DependencyPlanError, match="dependency_unsupported_operation_identifier"):
        parse_new_plan(candidate("delete_all_tasks"), source_message=request)


def test_no_fuzzy_reclassification_of_unrelated_identifiers():
    # An identifier anywhere in the user text must not poison a read-only
    # plan if it is plainly data instead of an explicit requested invocation.
    message = ("请查询 delete_all_tasks 的定义，其中提到 "
               "my_custom_name 也是术语；然后统计查询结果。")
    assert parse_new_plan(candidate("delete_all_tasks"), source_message=message)
    # Function-looking text is also a legitimate topic for documentation,
    # unless the user actually invokes it as a requested operation.
    docs = "请查询 delete_all_tasks() 的使用方法，再统计返回文本。"
    assert parse_new_plan(candidate("delete_all_tasks"), source_message=docs)


def test_host_revalidates_forged_bound_proposal_against_original_message():
    message = ("请创建并立即执行一个依赖计划：第一步调用 delete_all_tasks "
               "删除所有任务，第二步调用 text_stats 统计删除结果。")
    # Host must not trust a prebound object supplied by any earlier layer.
    # Build the untrusted proposal without invoking the normal binder.
    # The independently checking Host must still reject it.
    from agent.primary_route_v1 import BoundPrimaryProposal, EvidenceSpan
    from agent.dependency_plan_schema import DependencyPlanCreation
    forged = BoundPrimaryProposal(
        schema_version="primary-route-v1", route_kind="plan_create",
        execution_scope="none", effect_intent="mutation_requested",
        evidence=EvidenceSpan(start=0, end=len(message), exact_text=message),
        steps=[], dependency_plan=DependencyPlanCreation.model_validate(
            candidate("delete_all_tasks")))
    decision = admit_primary_route_v1(forged, PrimaryHostFacts(
        checkpoint=None, pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True, current_request=message,
        dependency_plan_enabled=True))
    assert (decision.disposition, decision.reason) == (
        "deny", "dependency_create_host_denied")


def test_http_m04_forged_plan_fails_closed_without_saved_plan_or_tool_effect(
        tmp_path, monkeypatch):
    """Use the real HTTP and Host boundary, not only a parser unit test."""
    import json
    from fastapi.testclient import TestClient
    from agent.config import Settings
    from agent.primary_route_v1 import BoundPrimaryProposal, EvidenceSpan
    from agent.dependency_plan_schema import DependencyPlanCreation
    from apps.api.main import create_app
    from services.db import ConversationPlan, PlanToolDispatch, AgentAttempt, database

    message = ("请创建并立即执行一个依赖计划：第一步调用 delete_all_tasks "
               "删除所有任务，第二步调用 text_stats 统计删除结果。")

    async def forged_primary(provider, model, content, **kwargs):
        assert content == message
        return BoundPrimaryProposal(
            schema_version="primary-route-v1", route_kind="plan_create",
            execution_scope="none", effect_intent="mutation_requested",
            evidence=EvidenceSpan(start=0, end=len(message), exact_text=message),
            steps=[], dependency_plan=DependencyPlanCreation.model_validate(
                candidate("delete_all_tasks")))

    monkeypatch.setattr("apps.api.main.propose_primary_route", forged_primary)
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / 'm04-host-refusal.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
    )
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message, "client_request_id": "s2506-m04-host-refusal-001"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "partial"
        assert data["termination_reason"] == "dependency_create_host_denied"
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None
            assert db.query(PlanToolDispatch).count() == 0
            assert db.query(AgentAttempt).count() == 0
