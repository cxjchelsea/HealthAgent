"""Unified dual-profile lifecycle: identical authoritative Primary routing.

Primary model proposals are stubbed; Host, M4 dispatch, Turn persistence and
replay run through the real HTTP application. No Qwen model claims are made.
"""
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import ConversationPlan, database


@pytest.fixture(autouse=True)
def forbid_retired_reviewers_for_primary_http(monkeypatch):
    # The same function-level fail-on-call list is used for successful
    # plan lifecycles and pending write cancellation, not only refusals.
    retired = (
        "review_plan_cancellation", "propose_first_route",
        "review_plan_intent", "check_plan_edit_intent",
        "classify_pending_reply", "review_pending_reference_lookup",
        "review_unsupported_capability", "review_read_only_clarification",
        "reconcile_execution_policy", "review_plan_creation_reference",
        "review_missing_plan_reference", "review_plan_execution_authorization",
        "review_plan_execution_scope",
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("AC2: retired reviewer reached from Primary HTTP")
    for name in retired:
        monkeypatch.setattr("apps.api.main." + name, forbidden)
    monkeypatch.setattr("apps.api.main.UnderstandingService", forbidden)
    monkeypatch.setattr("apps.api.main.decide_execution_policy_detail", forbidden)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", forbidden)

CREATE = "请创建一个两步可执行计划：第一步计算 3+4，第二步计算 8+9。只保存，不执行。"
REVISE = "把已保存计划的第二步由8+9改成10+11，第一步不变。"
FIRST = "只执行已保存计划的第一步"
RESUME = "继续执行剩余步骤"
CANCEL = "请取消当前保存的计划，不执行任何步骤。"


def _route(kind, message, *, saved, count=None):
    data = {"schema_version": "primary-route-v1", "route_kind": kind,
            "execution_scope": "none", "effect_intent": "none"}
    if kind == "plan_create":
        data.update({
            "quote": "创建一个两步可执行计划",
            "effect_intent": "mutation_requested",
            "steps": [
                {"goal": "计算 3+4", "tool_name": "calculate",
                 "arguments": {"expression": "3+4"}, "quote": "计算 3+4"},
                {"goal": "计算 8+9", "tool_name": "calculate",
                 "arguments": {"expression": "8+9"}, "quote": "计算 8+9"},
            ],
        })
    elif kind == "plan_revise":
        data.update({"quote": "第二步由8+9改成10+11",
                     "effect_intent": "mutation_requested",
                     "step_number": 2,
                     "revision_old_argument": "8+9",
                     "revision_new_argument": "10+11"})
    elif kind == "plan_execute_first":
        data.update({"quote": "第一步", "effect_intent": "read_only",
                     "execution_scope": "first_step"})
    elif kind == "plan_resume":
        data.update({"quote": "剩余步骤", "effect_intent": "read_only",
                     "execution_scope": "remaining_steps"})
    elif kind == "plan_cancel":
        data.update({"quote": "取消当前保存的计划",
                     "effect_intent": "mutation_requested"})
    return bind_route_signal(RouteSignal.model_validate(data), message,
                             saved_plan_present=saved,
                             pending_action=None, saved_step_count=count)


@pytest.mark.parametrize("routing_profile", ["auto", "unified"])
def test_create_first_resume_same_primary_and_m4(routing_profile, tmp_path, monkeypatch):
    async def proposal(*args, **kwargs):
        content = args[2]
        kind = {CREATE: "plan_create",
                FIRST: "plan_execute_first",
                RESUME: "plan_resume"}[content]
        return _route(kind, content, saved=kwargs["saved_plan_present"],
                      count=kwargs["saved_step_count"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy semantic owner called after Primary selection")

    monkeypatch.setattr("apps.api.main.propose_primary_route", proposal)
    for name in ("propose_new_plan_route", "propose_saved_plan_route",
                 "review_plan_intent", "review_plan_execution_authorization",
                 "review_plan_execution_scope", "reconcile_execution_policy"):
        monkeypatch.setattr("apps.api.main." + name, forbidden)
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'lifecycle.db'}",
        routing_profile=routing_profile, model_provider="ollama_proxy",
        _env_file=None)
    assert config.primary_route_v1_active
    with TestClient(create_app(config)) as client:
        cid = client.post("/conversations").json()["id"]

        def send(message, rid):
            response = client.post(f"/conversations/{cid}/agent/messages",
                                   json={"content": message,
                                         "client_request_id": rid})
            assert response.status_code == 200, response.text
            return response.json()

        created = send(CREATE, "dual-profile-create-001")
        assert created["termination_reason"] == "plan_created_pending_execution"
        first = send(FIRST, "dual-profile-first-001")
        assert first["termination_reason"] == "pending_plan_steps"
        assert [s["status"] for s in first["plan"]] == ["completed", "pending"]
        resumed = send(RESUME, "dual-profile-resume-001")
        assert resumed["termination_reason"] == "plan_execution_completed"
        assert [s["status"] for s in resumed["plan"]] == ["completed", "completed"]
        replay = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": RESUME,
                                   "client_request_id": "dual-profile-resume-001"})
        assert replay.status_code == 409
        _, Session = database(config.database_url)
        with Session() as db:
            stored = db.get(ConversationPlan, cid)
            assert stored is not None
            assert [s["status"] for s in json.loads(stored.snapshot)["steps"]] == [
                "completed", "completed"]


@pytest.mark.parametrize("routing_profile", ["auto", "unified"])
def test_create_revise_cancel_same_primary_and_cas(routing_profile, tmp_path, monkeypatch):
    async def proposal(*args, **kwargs):
        content = args[2]
        kind = {CREATE: "plan_create", REVISE: "plan_revise",
                CANCEL: "plan_cancel"}[content]
        return _route(kind, content, saved=kwargs["saved_plan_present"],
                      count=kwargs["saved_step_count"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy route owner invoked")
    monkeypatch.setattr("apps.api.main.propose_primary_route", proposal)
    monkeypatch.setattr("apps.api.main.review_plan_revision", forbidden)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", forbidden)
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / 'revise_cancel.db'}",
        routing_profile=routing_profile, model_provider="ollama_proxy",
        _env_file=None)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]

        def send(message, rid):
            response = client.post(f"/conversations/{cid}/agent/messages",
                                   json={"content": message,
                                         "client_request_id": rid})
            assert response.status_code == 200, response.text
            return response.json()

        assert send(CREATE, "dual-revise-create-001")["termination_reason"] == (
            "plan_created_pending_execution")
        updated = send(REVISE, "dual-revise-edit-001")
        assert updated["termination_reason"] == "plan_revised_pending_execution"
        assert updated["plan"][1]["expected_arguments"] == {"expression": "10+11"}
        cancelled = send(CANCEL, "dual-revise-cancel-001")
        assert cancelled["termination_reason"] == "plan_cancelled"
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None
