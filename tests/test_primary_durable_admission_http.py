"""AC1/AC3: durable Primary proposal and Host decision before side effects."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import (
    RouteSignal, bind_route_signal, PrimaryRouteRejected)
from apps.api.main import create_app
from services.db import PrimaryRouteEvidence, ConversationTask, database
from services.primary_route_evidence import snapshot_digest, read_primary_evidence


MESSAGE = "请创建任务：提交周报"


def _settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'primary_evidence.db'}",
                    model_provider="ollama_proxy", routing_profile="unified")


def _proposal():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool", "quote": MESSAGE,
        "effect_intent": "mutation_requested", "execution_scope": "none",
        "write_tool_name": "create_task",
        "write_arguments": {"title": "提交周报"},
    }), MESSAGE, saved_plan_present=False, pending_action=None)


def test_primary_evidence_is_committed_before_task_side_effect(
        tmp_path, monkeypatch):
    cfg = _settings(tmp_path)
    original = __import__(
        "apps.api.main", fromlist=["execute_primary_write_v1"]
    ).execute_primary_write_v1
    observations = []

    async def primary(*args, **kwargs):
        return _proposal()

    def inspect_before_write(*args, **kwargs):
        _, Session = database(cfg.database_url)
        with Session() as db:
            rows = db.scalars(select(PrimaryRouteEvidence)).all()
            tasks = db.scalars(select(ConversationTask)).all()
            assert len(rows) == 1
            assert not tasks
            evidence = rows[0]
            assert evidence.turn_id == kwargs["turn_id"]
            assert evidence.route_kind == "ordinary_tool"
            assert evidence.schema_version == "primary-route-v1"
            assert evidence.disposition == "allow"
            assert evidence.admission_reason == "primary_v1_create_task_candidate"
            assert evidence.preturn_snapshot_digest == snapshot_digest(None)
            assert evidence.semantic_attempts == 0  # injected model stub
            observations.append(evidence.proposal_id)
        return original(*args, **kwargs)

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1",
                        inspect_before_write)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "primary-evidence-write-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "primary_v1_task_created"
        turn_id = response.json()["turn_id"]
        diag = client.get(f"/conversations/{cid}/turns/{turn_id}/diagnostics")
        assert diag.status_code == 200
        saved = diag.json()["primary_route_evidence"]
        assert saved["proposal_id"] == observations[0]
        assert saved["route_kind"] == "ordinary_tool"
        assert saved["disposition"] == "allow"
        assert "title" not in str(saved)
        assert "提交周报" not in str(saved)
        replay = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "primary-evidence-write-001"})
        assert replay.status_code == 409
        with database(cfg.database_url)[1]() as db:
            assert len(db.scalars(select(PrimaryRouteEvidence)).all()) == 1
            assert len(db.scalars(select(ConversationTask)).all()) == 1


def test_rejected_primary_has_durable_refusal_no_second_owner(
        tmp_path, monkeypatch):
    cfg = _settings(tmp_path)

    async def rejected(*args, **kwargs):
        raise PrimaryRouteRejected("primary_route_invalid_signal")

    def no_dispatch(*args, **kwargs):
        raise AssertionError("invalid proposal must not dispatch")

    monkeypatch.setattr("apps.api.main.propose_primary_route", rejected)
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1", no_dispatch)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", no_dispatch)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "primary-evidence-refused-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "primary_v1_proposal_refused"
        diag = client.get(
            f"/conversations/{cid}/turns/{response.json()['turn_id']}/diagnostics")
        record = diag.json()["primary_route_evidence"]
        assert record["disposition"] == "refused"
        assert record["route_kind"] is None
        assert record["schema_version"] is None
        assert record["admission_reason"] == "primary_v1_proposal_refused"


def test_evidence_owner_scoped_and_cannot_be_mutated(tmp_path, monkeypatch):
    from services.primary_route_evidence import record_primary_admission

    cfg = _settings(tmp_path)

    async def primary(*args, **kwargs):
        return _proposal()

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "primary-evidence-owner-001"})
        assert response.status_code == 200, response.text
        tid = response.json()["turn_id"]
        other = client.post("/conversations").json()["id"]
        assert client.get(
            f"/conversations/{other}/turns/{tid}/diagnostics").status_code == 404
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert read_primary_evidence(
                db, conversation_id=other, turn_id=tid) is None
        with pytest.raises(RuntimeError, match="primary_evidence_turn_not_running"):
            record_primary_admission(
                session_factory=Session, conversation_id=cid, turn_id=tid,
                preturn_plan_raw=None, refusal_reason="retry", semantic_attempts=0)
        with Session() as db:
            assert db.get(PrimaryRouteEvidence, tid).disposition == "allow"



def test_provider_failure_is_durable_refusal_without_legacy(tmp_path, monkeypatch):
    cfg = _settings(tmp_path)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("simulated_model_transport_failure")

    def forbidden(*args, **kwargs):
        raise AssertionError("cannot execute or invoke second semantic owner")

    monkeypatch.setattr("apps.api.main.propose_primary_route", unavailable)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1", forbidden)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "primary-provider-failed-001"})
        assert response.status_code == 503, response.text
        tid = response.json()["detail"]["turn_id"]
        diag = client.get(f"/conversations/{cid}/turns/{tid}/diagnostics")
        assert diag.status_code == 200, diag.text
        record = diag.json()["primary_route_evidence"]
        assert record["route_kind"] is None
        assert record["disposition"] == "refused"
        assert record["admission_reason"] == "primary_v1_admission_unavailable"
        with database(cfg.database_url)[1]() as db:
            assert db.scalars(select(ConversationTask)).all() == []


def test_exception_after_admission_never_overwrites_original_record(
        tmp_path, monkeypatch):
    cfg = _settings(tmp_path)

    async def primary(*args, **kwargs):
        return _proposal()

    def crashed(*args, **kwargs):
        raise RuntimeError("simulated_pre_effect_crash")

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1", crashed)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": MESSAGE, "client_request_id": "primary-crash-after-admit-001"})
        assert response.status_code == 503, response.text
        tid = response.json()["detail"]["turn_id"]
        diag = client.get(f"/conversations/{cid}/turns/{tid}/diagnostics")
        assert diag.status_code == 200
        assert diag.json()["primary_route_evidence"]["disposition"] == "allow"
        with database(cfg.database_url)[1]() as db:
            assert len(db.scalars(select(PrimaryRouteEvidence)).all()) == 1
            assert db.scalars(select(ConversationTask)).all() == []



def test_primary_host_only_denial_is_recorded_without_semantic_model(
        tmp_path, monkeypatch):
    cfg = _settings(tmp_path)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Host-denied request cannot invoke model")

    monkeypatch.setattr("apps.api.main.propose_primary_route", forbidden)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请计算 10**100000",
            "selected_ordinary_operation": "calculate",
            "client_request_id": "primary-host-only-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == (
            "explicit_ordinary_admission_denied")
        diag = client.get(
            f"/conversations/{cid}/turns/{response.json()['turn_id']}/diagnostics")
        assert diag.status_code == 200
        record = diag.json()["primary_route_evidence"]
        assert record["route_kind"] is None
        assert record["disposition"] == "refused"
        assert record["semantic_attempts"] == 0
        assert record["admission_reason"] == "primary_v1_host_only_precheck"
