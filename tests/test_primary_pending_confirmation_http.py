"""Physical HTTP contract for explicit one-shot Pending write confirmation.

A model's clarify_response only stages a candidate. A separately authenticated
structured confirmation commits exactly one task and consumes the durable
authority. No textual 'yes' instruction is an authorization mechanism.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select
from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import (
    AgentAttempt, ConversationTask, PendingWriteAuthority, database,
)

ORIGIN = "请准备创建一项待办，但标题尚未确定，请先问我。"
TITLE = "整理合成实验台的登记信息"
UNRELATED = "给我讲讲微服务架构的用途"


def _settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'pending_confirmation.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        pending_write_confirmation_enabled=True, _env_file=None)


def _routes(monkeypatch):
    async def primary(*args, **kwargs):
        message = args[2]
        if message == ORIGIN:
            spec = {
                "schema_version": "primary-route-v1",
                "route_kind": "clarify", "quote": ORIGIN,
                "clarification_question": "请补充待办标题。",
                "pending_write_tool_name": "create_task",
            }
        else:
            assert message in (TITLE, UNRELATED)
            assert kwargs["pending_action"] == "create_task"
            # Even a wrong Qwen route must not be an execution authority.
            spec = {"schema_version": "primary-route-v1",
                    "route_kind": "clarify_response", "quote": message}
        return bind_route_signal(
            RouteSignal.model_validate(spec), message,
            saved_plan_present=False, pending_action=kwargs["pending_action"],
            pending_origin_verified=kwargs["pending_origin_verified"])
    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)


def _send(client, cid, content, req):
    return client.post(f"/conversations/{cid}/agent/messages", json={
        "content": content, "client_request_id": req})


def _counts(cfg, cid):
    _, Session = database(cfg.database_url)
    with Session() as db:
        authorities = db.scalars(select(PendingWriteAuthority).where(
            PendingWriteAuthority.conversation_id == cid)).all()
        tasks = db.scalars(select(ConversationTask).where(
            ConversationTask.conversation_id == cid)).all()
        attempts = db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all()
        return authorities, tasks, attempts


def test_pending_write_requires_explicit_confirm_and_is_one_shot(
        tmp_path, monkeypatch):
    _routes(monkeypatch)
    cfg = _settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        first = _send(client, cid, ORIGIN, "confirmation-origin-001")
        assert first.status_code == 200, first.text
        assert first.json()["termination_reason"] == "needs_input"

        staged = _send(client, cid, TITLE, "confirmation-reply-001")
        assert staged.status_code == 200, staged.text
        result = staged.json()
        assert result["termination_reason"] == "pending_write_confirmation_required"
        assert result["status"] == "partial"
        token = result["confirmation"]
        auths, tasks, attempts = _counts(cfg, cid)
        assert len(auths) == 1
        assert auths[0].status == "awaiting_confirmation"
        assert len(tasks) == len(attempts) == 0

        endpoint = f"/conversations/{cid}/pending-task/confirm"
        payload = {**token, "client_request_id": "confirmation-commit-001"}
        # An explicit structured action is necessary, and content changes
        # cannot be silently invented by a model.
        bad = client.post(endpoint, json={
            **payload, "confirmation_nonce": "00000000-0000-0000-0000-000000000000"})
        assert bad.status_code == 409
        auths, tasks, attempts = _counts(cfg, cid)
        assert auths[0].status == "awaiting_confirmation"
        assert len(tasks) == len(attempts) == 0

        confirmed = client.post(endpoint, json=payload)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["termination_reason"] == "pending_confirmed_task_created"
        assert client.post(endpoint, json=payload).status_code == 409
        auths, tasks, attempts = _counts(cfg, cid)
        assert auths[0].status == "consumed"
        assert len(tasks) == len(attempts) == 1
        assert tasks[0].title == TITLE
        assert attempts[0].status == "succeeded"
        assert attempts[0].tool_name == "create_task"


def test_wrong_semantic_reply_can_only_stage_not_write(
        tmp_path, monkeypatch):
    _routes(monkeypatch)
    cfg = _settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        assert _send(client, cid, ORIGIN, "confirmation-origin-002").status_code == 200
        second = _send(client, cid, UNRELATED, "confirmation-reply-002")
        assert second.status_code == 200, second.text
        # No punctuation, so legacy shape guard would accept it. Even then,
        # the model's mistaken semantic route is NOT enough for a write.
        assert second.json()["termination_reason"] == "pending_write_confirmation_required"
        auths, tasks, attempts = _counts(cfg, cid)
        assert auths[0].status == "awaiting_confirmation"
        assert tasks == [] and attempts == []

        other = client.post("/conversations").json()["id"]
        wrong_owner = client.post(
            f"/conversations/{other}/pending-task/confirm",
            json={**second.json()["confirmation"],
                  "client_request_id": "confirmation-cross-conversation"})
        assert wrong_owner.status_code == 409
        auths, tasks, attempts = _counts(cfg, cid)
        assert auths[0].status == "awaiting_confirmation"
        assert tasks == [] and attempts == []


def test_explicit_reject_revokes_candidate_without_write(tmp_path, monkeypatch):
    _routes(monkeypatch)
    cfg = _settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        assert _send(client, cid, ORIGIN, "confirmation-origin-003").status_code == 200
        staged = _send(client, cid, TITLE, "confirmation-reply-003")
        assert staged.status_code == 200, staged.text
        token = staged.json()["confirmation"]
        reject = client.post(
            f"/conversations/{cid}/pending-task/reject",
            json={**token, "client_request_id": "confirmation-reject-003"})
        assert reject.status_code == 200, reject.text
        assert reject.json()["status"] == "revoked"
        confirm = client.post(
            f"/conversations/{cid}/pending-task/confirm",
            json={**token, "client_request_id": "confirmation-commit-003"})
        assert confirm.status_code == 409
        auths, tasks, attempts = _counts(cfg, cid)
        assert auths[0].status == "revoked"
        assert tasks == [] and attempts == []
