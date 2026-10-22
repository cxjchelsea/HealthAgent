"""S25-01 minimal goal-state roundtrip and real API history integration."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel, UnderstandingResult
from apps.api.main import create_app
from services.db import database
from services.goal_store import ConversationGoalStore

def proposal(kind, objective, constraints=None):
    return json.dumps({
        "status": "understood",
        "goal": {"objective": objective, "subgoals": [],
                 "constraints": constraints or [], "success_criteria": []},
        "change": {"kind": kind, "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
        "schema_version": "s25-01-v1"}, ensure_ascii=False)

class SimpleDecider:
    async def decide(self, messages, tools):
        return Decision("收到", [])

def test_goal_is_durable_and_visible_on_next_turn(tmp_path, monkeypatch):
    seen = []
    async def fake_complete(self, *, messages, model):
        data = json.loads(messages[-1]["content"])
        seen.append(data)
        if len(seen) == 1:
            return proposal("new_goal", "安排三项任务", [
                {"name": "limit", "value": "最多三项",
                 "source": "explicit", "scope": "goal"}])
        return proposal("continue", "安排三项任务")
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'goal.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=SimpleDecider())) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        for n, utterance in enumerate(["安排明天最多三项任务", "继续刚才的目标"], 1):
            response = client.post(path, json={
                "content": utterance, "client_request_id": f"goal-state-round-{n:03d}"})
            assert response.status_code == 200, response.text
        assert seen[1]["active_goal"]["objective"] == "安排三项任务"
        assert seen[1]["confirmed_constraints"][0]["value"] == "最多三项"
        engine, Session = database(settings.database_url)
        state = ConversationGoalStore(Session, cid).load()
        assert state["version"] == 2
        assert state["goal"]["objective"] == "安排三项任务"
        engine.dispose()

def test_goal_state_change_requires_consistent_version(tmp_path):
    from services.migrate import migrate
    url = f"sqlite:///{tmp_path/'cas.db'}"
    migrate(url)
    engine, Session = database(url)
    from services.db import Conversation
    with Session.begin() as db:
        convo = Conversation(owner_id="owner")
        db.add(convo)
        db.flush()
        cid = convo.id
    store = ConversationGoalStore(Session, cid)
    original = store.load()
    result = UnderstandingResult.model_validate_json(proposal("new_goal", "制定计划"))
    with Session.begin() as db:
        store.save_in_session(db, result, original)
    with __import__("pytest").raises(RuntimeError, match="stale_goal_version"):
        with Session.begin() as db:
            store.save_in_session(db, result, original)
    engine.dispose()

def test_explicit_constraints_cannot_be_replaced_without_preflight(tmp_path):
    from services.migrate import migrate
    from services.db import Conversation
    from agent.understanding import UnderstandingError
    import pytest
    url = f"sqlite:///{tmp_path/'constraint.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        conversation = Conversation(owner_id="owner")
        db.add(conversation)
        db.flush()
        cid = conversation.id
    store = ConversationGoalStore(Session, cid)
    baseline = store.load()
    original = UnderstandingResult.model_validate_json(proposal("new_goal", "安排三项", [
        {"name": "limit", "value": "最多三项", "source": "explicit", "scope": "goal"}]))
    with Session.begin() as db:
        store.save_in_session(db, original, baseline)
    current = store.load()
    revision = UnderstandingResult.model_validate_json(proposal("revise", "安排两项", [
        {"name": "limit", "value": "最多两项", "source": "explicit", "scope": "goal"}]))
    assert store.validate_before_execution(revision, current) == "confirmation_required"
    assert store.load() == current
    engine.dispose()

def test_pending_clarification_can_be_resumed_with_goal(tmp_path):
    from services.migrate import migrate
    from services.db import Conversation
    url = f"sqlite:///{tmp_path/'pending.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        convo = Conversation(owner_id="owner")
        db.add(convo)
        db.flush()
        cid = convo.id
    store = ConversationGoalStore(Session, cid)
    pending = UnderstandingResult.model_validate({
        "status": "clarification_required",
        "goal": {"objective": "整理计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": True, "reason_code": "missing_critical_constraint",
                          "question": "具体是哪天？"},
        "schema_version": "s25-01-v1"
    })
    with Session.begin() as db:
        store.save_in_session(db, pending, store.load())
    before = store.load()
    assert before["pending"]["clarification"]["question"] == "具体是哪天？"
    updated = UnderstandingResult.model_validate_json(
        proposal("clarify_response", "整理明天计划"))
    store.validate_before_execution(updated, before)
    with Session.begin() as db:
        store.save_in_session(db, updated, before)
    after = store.load()
    assert after["pending"] is None
    assert after["goal"]["objective"] == "整理明天计划"
    engine.dispose()

def test_api_constraint_revision_requires_explicit_versioned_confirmation(tmp_path, monkeypatch):
    calls = []
    async def fake_complete(self, *, messages, model):
        calls.append(model)
        if len(calls) == 1:
            return proposal("new_goal", "安排三项", [
                {"name": "limit", "value": "最多三项", "source": "explicit", "scope": "goal"}])
        return proposal("revise", "安排两项", [
            {"name": "limit", "value": "最多两项", "source": "explicit", "scope": "goal"}])
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'confirm.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=SimpleDecider())) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        first = client.post(route, json={"content": "安排最多三项",
            "client_request_id": "revision-first-001"})
        assert first.status_code == 200, first.text
        second = client.post(route, json={"content": "改为最多两项",
            "client_request_id": "revision-second-002"})
        assert second.status_code == 200, second.text
        result = second.json()
        assert result["status"] == "partial"
        assert result["expected_goal_version"] == 2
        engine, Session = database(settings.database_url)
        store = ConversationGoalStore(Session, cid)
        pending = store.load()
        assert pending["goal"]["constraints"][0]["value"] == "最多三项"
        assert pending["pending"]["goal"]["constraints"][0]["value"] == "最多两项"
        stale = client.post(route, json={"content": "我确认修改",
            "client_request_id": "revision-stale-003",
            "goal_confirmation": "accept", "expected_goal_version": 1})
        assert stale.status_code == 503
        assert store.load() == pending
        confirm = client.post(route, json={"content": "我确认修改",
            "client_request_id": "revision-confirm-004",
            "goal_confirmation": "accept", "expected_goal_version": 2})
        assert confirm.status_code == 200, confirm.text
        final = store.load()
        assert final["goal"]["constraints"][0]["value"] == "最多两项"
        assert final["pending"] is None
        assert len(calls) == 2
        engine.dispose()

def test_model_clarification_cannot_confirm_pending_revision(tmp_path):
    from services.migrate import migrate
    from services.db import Conversation
    from agent.understanding import UnderstandingError
    import pytest
    url = f"sqlite:///{tmp_path/'no-bypass.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        conversation = Conversation(owner_id="owner")
        db.add(conversation)
        db.flush()
        cid = conversation.id
    store = ConversationGoalStore(Session, cid)
    initial = UnderstandingResult.model_validate_json(proposal("new_goal", "安排三项", [
        {"name": "limit", "value": "最多三项", "source": "explicit", "scope": "goal"}]))
    with Session.begin() as db:
        store.save_in_session(db, initial, store.load())
    revision = UnderstandingResult.model_validate_json(proposal("revise", "安排两项", [
        {"name": "limit", "value": "最多两项", "source": "explicit", "scope": "goal"}]))
    with Session.begin() as db:
        store.save_in_session(db, revision, store.load())
    before = store.load()
    disguised = UnderstandingResult.model_validate_json(proposal("clarify_response", "安排两项", [
        {"name": "limit", "value": "最多两项", "source": "explicit", "scope": "goal"}]))
    with pytest.raises(UnderstandingError, match="pending_revision_requires_explicit_confirmation"):
        store.validate_before_execution(disguised, before)
    assert store.load() == before
    engine.dispose()

def test_add_cannot_rewrite_or_drop_prior_subgoals(tmp_path):
    from services.migrate import migrate
    from services.db import Conversation
    from agent.understanding import UnderstandingError
    import pytest
    url = f"sqlite:///{tmp_path/'add_preserve.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        convo = Conversation(owner_id="owner")
        db.add(convo)
        db.flush()
        cid = convo.id
    store = ConversationGoalStore(Session, cid)
    base = UnderstandingResult.model_validate_json(proposal("new_goal", "明日任务"))
    base.goal.subgoals = ["审查资料", "完成计算", "整理总结"]
    with Session.begin() as db:
        store.save_in_session(db, base, store.load())
    previous = store.load()

    invalid = UnderstandingResult.model_validate_json(proposal("add", "明日任务"))
    invalid.goal.subgoals = ["周末买菜"]
    with pytest.raises(UnderstandingError, match="add_must_preserve_existing_goal"):
        store.validate_before_execution(invalid, previous)
    assert store.load() == previous

    valid = UnderstandingResult.model_validate_json(proposal("add", "明日任务"))
    valid.goal.subgoals = ["审查资料", "完成计算", "整理总结", "周末买菜"]
    assert store.validate_before_execution(valid, previous) == "ok"
    with Session.begin() as db:
        store.save_in_session(db, valid, previous)
    assert store.load()["goal"]["subgoals"] == valid.goal.subgoals
    engine.dispose()
