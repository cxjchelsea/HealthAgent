"""Shared M5 saved-plan route semantics: source-bound and never authoritative."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.m3_turn_capture import capture_turn
from agent.m5_unified_route import (
    SavedPlanRouteRefused, propose_saved_plan_route,
)
from apps.api.main import create_app
from services.db import ConversationPlan, database


class Provider:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return json.dumps(self.value, ensure_ascii=False)


def invoke(route, message, quote, *, corrupt=False, checkpoint=None):
    checkpoint = checkpoint or {"revision": 1, "steps": [{"id": 1, "status": "pending"}]}
    index = message.find(quote)
    if index < 0:
        index = 0  # Feed fabricated quote to Host, not fail in test helper.
    cap = capture_turn(conversation_id="c", request_id="req", turn_id="t",
                       checkpoint=checkpoint, pending_action=None,
                       clarification_id=None, pending_question=None)
    provider = Provider({
        "route": route, "quote": quote,
        "start": index + (1 if corrupt else 0), "end": index + len(quote),
    })
    decision = asyncio.run(propose_saved_plan_route(
        provider, "test", message, cap, conversation_id="c",
        request_id="req", turn_id="t", checkpoint=checkpoint))
    return decision, provider.calls


@pytest.mark.parametrize("route,message,quote", [
    ("plan_execute_first", "请只执行第一步", "第一步"),
    ("plan_resume", "请继续执行剩余步骤", "剩余步骤"),
    ("plan_revise", "把第二步改成计算8*8", "第二步"),
    ("plan_cancel", "请取消当前保存的计划", "取消当前保存的计划"),
    ("other", "今天天气怎么样", "天气"),
])
def test_single_owner_selects_exactly_one_route(route, message, quote):
    assert invoke(route, message, quote) == (route, 1)


def test_bad_source_never_authorizes_action():
    # Model arithmetic is advisory; an exact quote is Host-located.
    assert invoke("plan_cancel", "请取消当前保存的计划",
                  "取消当前保存的计划", corrupt=True) == ("plan_cancel", 1)
    # A fabricated or repeated quote is never usable as evidence.
    with pytest.raises(SavedPlanRouteRefused):
        invoke("plan_cancel", "请取消当前保存的计划", "不存在的引文")
    with pytest.raises(SavedPlanRouteRefused):
        invoke("plan_cancel", "请取消当前保存的计划并取消步骤",
               "取消")


def test_captured_snapshot_is_not_execution_authority():
    from agent.route_primary_m5_capture import bind_captured_proposal
    from agent.route_primary_m5 import PrimarySemanticProposal
    from agent.route_primary_m5_binding import ProposalBindingRefused
    msg = "请继续执行剩余步骤"
    snap = {"revision": 1, "steps": [{"id": 1, "status": "pending"}]}
    cap = capture_turn(conversation_id="c", request_id="req", turn_id="t",
                       checkpoint=snap, pending_action=None,
                       clarification_id=None, pending_question=None)
    quote = "剩余步骤"
    start = msg.index(quote)
    proposal = PrimarySemanticProposal.model_validate({
        "schema_version": "s25-m5-primary-v1", "route": "plan_resume",
        "scope": "remaining_steps", "effect_intent": "read_only",
        "ambiguity": "clear", "reference": {"kind": "saved_plan"},
        "operation": {"route": "plan_resume", "scope": "remaining_steps",
                      "evidence": [{"start": start, "end": start + len(quote),
                                    "exact_text": quote}]},
    })
    with pytest.raises(ProposalBindingRefused):
        bind_captured_proposal(
            proposal, msg, cap, conversation_id="c",
            request_id="req", turn_id="wrong", checkpoint=snap)


def test_guarded_api_cancel_uses_shared_owner_not_second_cancel_classifier(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'm5_unified.db'}",
                        model_provider="fake", route_admission_mode="guarded",
                        understanding_strategy="structured",
                        m5_unified_route_enabled=True,
                        m5_first_route_enabled=False)
    events = {"primary": 0, "old_cancel": 0}

    async def primary(*args, **kwargs):
        events["primary"] += 1
        return "plan_cancel"

    async def old_cancel(*args, **kwargs):
        events["old_cancel"] += 1
        return True

    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", old_cancel)
    monkeypatch.setattr("apps.api.main.admit_guarded_plan_cancel",
                        lambda **kwargs: True)

    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        checkpoint = {"revision": 1, "next_step_id": 3, "steps": [
            {"id": 1, "goal": "计算 3*4", "status": "pending",
             "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
            {"id": 2, "goal": "计算 5*6", "status": "pending",
             "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}},
        ]}
        with Session.begin() as db:
            db.add(ConversationPlan(
                conversation_id=cid, snapshot=json.dumps(checkpoint, ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请取消当前保存的计划",
            "client_request_id": "m5-unified-cancel-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "plan_cancelled"
        assert not any(t.get("type") == "tool" for t in response.json()["trace"])
        assert events == {"primary": 1, "old_cancel": 0}


@pytest.mark.parametrize("selected,expected", [
    ("plan_revise", "plan_revision_conflict"),
    ("refused", "m5_unified_binding_refused"),
])
def test_unified_failed_binding_or_revision_never_executes_tools(
        tmp_path, monkeypatch, selected, expected):
    from agent.plan_revision_review import RevisionProposal
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'denial.db'}",
                        model_provider="fake", route_admission_mode="guarded",
                        understanding_strategy="structured",
                        m5_unified_route_enabled=True)

    async def primary(*args, **kwargs):
        if selected == "refused":
            raise SavedPlanRouteRefused("bad current turn")
        return selected

    async def inconclusive_revision(*args, **kwargs):
        return RevisionProposal(intent="other", edit_requested=False)

    async def should_not_classify(*args, **kwargs):
        raise AssertionError("legacy route classifier should not override M5")

    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_revision", inconclusive_revision)
    monkeypatch.setattr("apps.api.main.review_plan_intent", should_not_classify)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        checkpoint = {"revision": 1, "next_step_id": 3, "steps": [
            {"id": 1, "goal": "计算 3*4", "status": "pending",
             "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
            {"id": 2, "goal": "计算 5*6", "status": "pending",
             "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}},
        ]}
        with Session.begin() as db:
            db.add(ConversationPlan(conversation_id=cid,
                                    snapshot=json.dumps(checkpoint, ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "把第二步计算5*6改成计算8*8",
            "client_request_id": "m5-unified-denial-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == expected
        assert not any(t.get("type") == "tool" for t in response.json()["trace"])
        with Session() as db:
            row = db.get(ConversationPlan, cid)
            assert json.loads(row.snapshot) == checkpoint


def test_unified_resume_scope_disagreement_cannot_execute_first(tmp_path, monkeypatch):
    from agent.execution_policy import ExecutionPolicyDecision
    from agent.understanding import HTTPUnderstandingModel

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'scope.db'}",
                        model_provider="ollama_proxy",
                        route_admission_mode="guarded",
                        understanding_strategy="structured",
                        m5_unified_route_enabled=True)
    counter = {"scope": 0, "authorization": 0, "tools": 0}

    async def primary(*args, **kwargs):
        return "plan_resume"

    async def confirmed(*args, **kwargs):
        counter["authorization"] += 1
        return True

    async def downscope(*args, **kwargs):
        counter["scope"] += 1
        return "one"

    async def policy(*args, **kwargs):
        return ExecutionPolicyDecision(mode="read_only", target="tool_action")

    async def complete(self, *, model, messages, response_schema=None):
        return json.dumps({
            "schema_version": "s25-01-v1", "status": "understood",
            "goal": {"objective": "继续执行计划", "subgoals": [],
                     "constraints": [], "success_criteria": []},
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
        }, ensure_ascii=False)

    def no_tools(*args, **kwargs):
        counter["tools"] += 1
        raise AssertionError("unverified resume must not dispatch")

    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_execution_authorization", confirmed)
    monkeypatch.setattr("apps.api.main.review_plan_execution_scope", downscope)
    monkeypatch.setattr("apps.api.main.decide_execution_policy_detail", policy)
    monkeypatch.setattr("apps.api.main.execute_tool", no_tools)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)

    checkpoint = {"revision": 1, "next_step_id": 3, "steps": [
        {"id": 1, "goal": "计算 3*4", "status": "pending",
         "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
        {"id": 2, "goal": "计算 5*6", "status": "pending",
         "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}},
    ]}
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            db.add(ConversationPlan(conversation_id=cid,
                                    snapshot=json.dumps(checkpoint, ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请继续执行剩余步骤",
            "client_request_id": "m5-unified-resume-scope-001",
        })
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "m5_unified_scope_unverified"
        assert response.json()["steps"] == 0
        assert counter == {"scope": 1, "authorization": 1, "tools": 0}
        with Session() as db:
            assert json.loads(db.get(ConversationPlan, cid).snapshot) == checkpoint


def test_unified_revision_uncertain_parameters_skip_legacy_edit_classifier(
        tmp_path, monkeypatch):
    from agent.plan_revision_review import RevisionProposal

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'revision.db'}",
                        model_provider="fake", route_admission_mode="guarded",
                        understanding_strategy="structured",
                        m5_unified_route_enabled=True)
    async def primary(*args, **kwargs):
        return "plan_revise"

    async def missing_arguments(*args, **kwargs):
        return RevisionProposal(intent="other", edit_requested=None)

    async def forbidden(*args, **kwargs):
        raise AssertionError("duplicate legacy edit-intent review")

    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_plan_revision", missing_arguments)
    monkeypatch.setattr("apps.api.main.check_plan_edit_intent", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", forbidden)

    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        checkpoint = {"revision": 1, "next_step_id": 3, "steps": [
            {"id": 1, "goal": "计算 3*4", "status": "pending",
             "expected_tool": "calculate", "expected_arguments": {"expression": "3*4"}},
            {"id": 2, "goal": "计算 5*6", "status": "pending",
             "expected_tool": "calculate", "expected_arguments": {"expression": "5*6"}},
        ]}
        with Session.begin() as db:
            db.add(ConversationPlan(conversation_id=cid,
                                    snapshot=json.dumps(checkpoint, ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "把第二步改一改",
            "client_request_id": "m5-unified-revise-uncertain-001",
        })
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "plan_revision_conflict"
        with Session() as db:
            assert json.loads(db.get(ConversationPlan, cid).snapshot) == checkpoint
