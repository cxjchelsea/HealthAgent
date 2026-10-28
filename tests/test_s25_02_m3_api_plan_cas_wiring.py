"""Guard safe CAS wiring for no-effect plan outcomes in the real API path."""
from pathlib import Path


def test_turn_uses_plan_snapshot_read_inside_creation_transaction():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    route = source.index("async def send_agent_in_conversation")
    capture = source.index("preturn_plan_raw = plan_record.snapshot if plan_record else None", route)
    legacy_decisions = source.index("existing_checkpoint = (json.loads(preturn_plan_raw)", capture)
    assert capture < legacy_decisions
    assert "existing_checkpoint = plan_store.load()" not in source[route:legacy_decisions]


def test_no_effect_plan_outcomes_use_exact_snapshot_cas():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    assert '"plan_created_pending_execution"' in source
    assert '"plan_revised_pending_execution"' in source
    assert '"plan_already_completed"' in source
    assert '"plan_first_step_already_completed"' in source
    assert "plan_store.compare_and_swap_in_session(" in source
    assert "expected_raw=preturn_plan_raw" in source
    assert "snapshot=result[\"plan_checkpoint\"]" in source


def test_all_plan_checkpoints_use_cas_and_saved_plan_dispatch_has_guard():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    assert "plan_store.compare_and_swap_in_session(" in source
    assert "expected_raw=preturn_plan_raw" in source
    assert "saved_plan_execution_guard" in source
    assert 'plan_control in {"execute_first", "resume"}' in source
    assert "plan_store.save_in_session(db, result[\"plan_checkpoint\"])" not in source


def test_stale_no_effect_plan_write_returns_conflict_without_stuck_running_turn():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    conflict = source.index("except PlanSnapshotConflict:", source.index(
        "async def send_agent_in_conversation"))
    finalized = source.index("turn.state = status", conflict)
    assert 'failure_code = "plan_snapshot_conflict"' in source[conflict:finalized]
    assert 'termination_reason": "plan_snapshot_conflict"' in source[conflict:finalized]
    assert finalized > conflict


def test_saved_plan_execution_uses_durable_claim_and_owner_bound_cas():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    assert "acquire_execution_claim_in_session(" in source
    assert "release_execution_claim_in_session(db, turn_id=turn_id)" in source
    assert "owner_turn_id=turn_id if plan_control in" in source
    store_source = Path("services/plan_store.py").read_text(encoding="utf-8")
    assert "PlanExecutionClaim" in store_source
    assert "self._assert_claim_owner(db, owner_turn_id)" in store_source
