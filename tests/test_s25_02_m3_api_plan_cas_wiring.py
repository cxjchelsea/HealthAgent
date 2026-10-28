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


def test_plan_execution_checkpoint_is_not_misrepresented_as_cas_protected():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    marker = "# Tool-execution checkpoints intentionally remain on the legacy"
    assert marker in source
    assert "plan_store.save_in_session(db, result[\"plan_checkpoint\"])" in source


def test_stale_no_effect_plan_write_returns_conflict_without_stuck_running_turn():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    conflict = source.index("except PlanSnapshotConflict:", source.index(
        "async def send_agent_in_conversation"))
    finalized = source.index("turn.state = status", conflict)
    assert 'failure_code = "plan_snapshot_conflict"' in source[conflict:finalized]
    assert 'termination_reason": "plan_snapshot_conflict"' in source[conflict:finalized]
    assert finalized > conflict
