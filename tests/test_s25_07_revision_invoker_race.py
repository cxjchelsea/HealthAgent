"""Independent S25-07 adversarial dual-invoker race checks.

The same committed Dispatch must cause at most ONE actual tool invocation
under SQLite (which ignores SELECT FOR UPDATE) and PostgreSQL.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
import time

import pytest
from sqlalchemy.exc import OperationalError

from agent.dependency_plan_schema import DependencyPlanError
from agent.mcp_stdio_client import StdioMCPClient
from agent.tool_registry import READ_REGISTRY
from services.db import PlanToolDispatch
from services.dependency_revision_first_mcp_runner import (
    invoke_first_revised_mcp_reference,
)
from services.dependency_revision_registry_runner import (
    invoke_second_revised_registry_read,
)
from test_s25_07_gate_b3c3_result import store, _prepared
from test_s25_07_revision_second import _prepare, _begin


@pytest.mark.parametrize("which", ["first_mcp", "second_stats"])
def test_single_invoker_wins_concurrent_dispatch(store, monkeypatch, which):
    Session, cid = store
    if which == "first_mcp":
        raw, binding, fence, cfg, did, _ = _prepared(
            Session, cid, completed=False)
        cfg = cfg.model_copy(update={
            "s25_07_revision_registry_invocation_enabled": True})
        def call():
            return invoke_first_revised_mcp_reference(
                Session, settings=cfg, binding=binding,
                expected_raw=raw, initial_turn_raw=raw,
                fence=fence, dispatch_id=did, mcp_client=StdioMCPClient())
    else:
        first, _, cfg, tid, decision, fence, raw = _prepare(Session, cid)
        did = _begin(Session, first, cfg, tid, decision, fence, raw)
        cfg = cfg.model_copy(update={
            "s25_07_revision_registry_invocation_enabled": True})
        def call():
            return invoke_second_revised_registry_read(
                Session, settings=cfg, owner_id=first.owner_id,
                conversation_id=cid, turn_id=tid,
                request_id="continuation-"+tid,
                expected_raw=raw, initial_turn_raw=raw,
                fence=fence, dispatch_id=did)

    calls = []
    lock = Lock()
    barrier = Barrier(2)
    def counted(name, arguments, **kwargs):
        with lock:
            calls.append(name)
        # Give the contender an opportunity to reach the same Dispatch while
        # the first worker has irrevocably marked it as invoking.
        time.sleep(0.12)
        if name == "mcp_reference_lookup":
            return {"ok": True, "definition": "RAG reference definition",
                    "tool_name": name, "tool_version": "1.0.0"}
        return {"ok": True, "characters": len(arguments["text"]),
                "words": len(arguments["text"].split()),
                "tool_name": name, "tool_version": "1.0.0"}
    monkeypatch.setattr(READ_REGISTRY, "invoke", counted)

    def contender():
        barrier.wait(timeout=5)
        try:
            return ("ok", call())
        except (DependencyPlanError, OperationalError) as exc:
            return ("denied", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(contender) for _ in range(2)]
        outcomes = [task.result(timeout=15) for task in tasks]

    assert sorted(o[0] for o in outcomes) == ["denied", "ok"]
    assert len(calls) == 1
    assert calls[0] == ("mcp_reference_lookup" if which == "first_mcp"
                         else "text_stats")
    with Session() as db:
        row = db.get(PlanToolDispatch, did)
        assert row.status == "observed"
        assert row.receipt_json is not None
