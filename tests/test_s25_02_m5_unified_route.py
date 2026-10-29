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
    index = message.index(quote)
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
    with pytest.raises(SavedPlanRouteRefused):
        invoke("plan_cancel", "请取消当前保存的计划", "取消当前保存的计划",
               corrupt=True)


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
