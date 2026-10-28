"""HTTP API integration for durable saved-plan execution claims.

The test exercises the real route, Turn persistence, database claim acquisition,
competing plan writes, owner checkpoint finalization, and claim release. Model
classification and the actual tool body are controlled to keep it deterministic.
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.decision import Decision
from agent.plan_control import PlanRequest
from agent.planning import PlanLedger
from agent.understanding import UnderstandingResult
from apps.api.main import create_app
from services.db import ConversationPlan, PlanExecutionClaim, Turn, database
from services.plan_store import ConversationPlanStore, PlanSnapshotConflict


def _understood():
    return UnderstandingResult.model_validate({
        "status": "understood",
        "goal": None,
        "change": {"kind": "chat", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
        "schema_version": "s25-01-v1",
    })


def _checkpoint():
    ledger = PlanLedger()
    assert ledger.propose([{
        "goal": "计算 8*8",
        "expected_tool": "calculate",
        "expected_arguments": {"expression": "8*8"},
    }])["ok"]
    return ledger.checkpoint()


class _UnusedDecider:
    async def decide(self, messages, tools):
        raise AssertionError("the controlled API path must not use a model tool decider")


def test_http_saved_plan_claim_blocks_competing_writer_and_releases_on_finalize(
        tmp_path, monkeypatch):
    import apps.api.main as api

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm3_claim_api.db'}",
        model_provider="fake",
        understanding_strategy="structured",
    )
    plan = _checkpoint()
    run_state = {"mode": "create", "guard_calls": 0, "competing_write_blocked": False}

    async def fake_understand(self, context):
        return {"result": _understood(), "attempts": []}

    async def fake_policy(*args, **kwargs):
        from agent.execution_policy import ExecutionPolicyDecision
        return ExecutionPolicyDecision(mode="execute", target="tool_action")

    async def fake_plan_intent(provider, model, message, existing_plan):
        if "创建" in message:
            return PlanRequest(intent="plan_only", steps=[{
                "goal": "计算 8*8", "expected_tool": "calculate",
                "expected_arguments": {"expression": "8*8"},
            }])
        return PlanRequest(intent="execute_first", steps=[])

    async def fake_create_review(*args, **kwargs):
        return True

    async def fake_execution_review(*args, **kwargs):
        return True

    async def fake_run(self, message, **kwargs):
        guard = kwargs.get("execution_guard")
        assert guard is not None, "saved-plan route must install the pre-dispatch guard"
        run_state["guard_calls"] += 1
        assert guard() is True
        # The guard's reservation must already be committed before dispatch.
        with self.session_factory.begin() as db:
            pass
        store = api.ConversationPlanStore(api.Session, self.conversation_id)
        observed = store.load_raw()
        try:
            with api.Session.begin() as db:
                store.compare_and_swap_in_session(
                    db, expected_raw=observed,
                    snapshot={"revision": 99, "steps": []})
        except PlanSnapshotConflict as exc:
            assert "plan_execution_claimed" in str(exc)
            run_state["competing_write_blocked"] = True
        else:
            raise AssertionError("competing plan writer unexpectedly passed the claim")

        # Return valid owner-bound progress. The route must CAS this checkpoint
        # and release the claim in the same terminal-turn transaction.
        from agent.planning import PlanLedger
        ledger = PlanLedger.restore(kwargs["plan_checkpoint"])
        result = ledger.execute_first(lambda name, args: {"ok": True, "value": 64})
        return {
            "run_id": "controlled",
            "status": "partial",
            "termination_reason": "plan_step_completed",
            "answer": "第一步完成",
            "trace": [{"step": 1, "type": "tool", "name": "calculate", "ok": True}],
            "steps": 1,
            "plan": ledger.snapshot(),
            "plan_revision": ledger.revision,
            "plan_events": [],
            "plan_checkpoint": ledger.checkpoint(),
        }

    monkeypatch.setattr(api, "review_plan_intent", fake_plan_intent)
    monkeypatch.setattr(api, "review_plan_creation_reference", fake_create_review)
    monkeypatch.setattr(api, "review_plan_execution_authorization", fake_execution_review)
    monkeypatch.setattr(api, "decide_execution_policy_detail", fake_policy)
    monkeypatch.setattr(api.UnderstandingService, "understand", fake_understand)
    monkeypatch.setattr(api.AgentLoop, "run", fake_run)

    with TestClient(create_app(settings, agent_decider=None)) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        created = client.post(path, json={
            "content": "创建计划：计算 8*8",
            "client_request_id": "m3-claim-api-create-001",
        })
        assert created.status_code == 200, created.text
        assert created.json()["termination_reason"] == "plan_created_pending_execution"

        executed = client.post(path, json={
            "content": "现在只执行已保存计划的第一步",
            "client_request_id": "m3-claim-api-execute-002",
        })
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "partial"
        assert run_state["guard_calls"] == 1
        assert run_state["competing_write_blocked"] is True

    engine, Session = database(settings.database_url)
    try:
        with Session() as db:
            saved = db.get(ConversationPlan, cid)
            claim = db.get(PlanExecutionClaim, cid)
            turns = list(db.scalars(select(Turn).where(Turn.conversation_id == cid)))
            assert saved is not None
            assert json.loads(saved.snapshot)["revision"] == 2
            assert claim is None, "terminal Turn must release its durable claim"
            assert len(turns) == 2
            assert all(turn.state in {"partial", "completed"} for turn in turns)
    finally:
        engine.dispose()
